from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from langgraph.types import Command

from .agents import SupervisorAgent
from .memory import SessionQuestionMemory
from .repository import RunRepository
from .schemas import (
    AutomaticCandidateRequest,
    AutomaticCandidateResponse,
    CreateRunRequest,
    ReviewDecision,
    RunAccepted,
    RunResponse,
    RunStatusResponse,
)


class RunNotFoundError(LookupError):
    pass


class InvalidRunStateError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


class OrchestrationService:
    def __init__(
        self,
        *,
        graph,
        repository: RunRepository,
        supervisor: SupervisorAgent,
        question_memory: SessionQuestionMemory | None = None,
    ) -> None:
        self._graph = graph
        self._repository = repository
        self._supervisor = supervisor
        self._question_memory = question_memory
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()
        self._active_run_ids: set[str] = set()

    @property
    def active_run_count(self) -> int:
        return len(self._active_run_ids)

    @staticmethod
    def _config(run_id: str) -> dict:
        return {"configurable": {"thread_id": run_id}, "recursion_limit": 100}

    def _track(self, run_id: str, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        self._active_run_ids.add(run_id)

        def cleanup(completed: asyncio.Task) -> None:
            self._tasks.discard(completed)
            self._active_run_ids.discard(run_id)

        task.add_done_callback(cleanup)

    async def enqueue_run(self, request: CreateRunRequest) -> RunAccepted:
        return await self._enqueue_run(request)

    async def assess_candidate(
        self, request: AutomaticCandidateRequest
    ) -> AutomaticCandidateResponse:
        recent_questions = (
            await self._question_memory.recent_questions(request.session_id)
            if self._question_memory is not None
            else []
        )
        phase_started = perf_counter()
        readiness = await self._supervisor.assess(
            source_text=request.text,
            selection=request.supervisor_model,
            force_decision=request.force_decision,
            recent_questions=recent_questions,
        )
        assessment_ms = round((perf_counter() - phase_started) * 1000)
        if readiness.decision != "ready":
            return AutomaticCandidateResponse(
                **readiness.model_dump(mode="json"),
                assessment_ms=assessment_ms,
            )

        accepted = await self._enqueue_run(
            request,
            supervisor_instruction=readiness.instruction,
            supervisor_reason=readiness.reason,
            assessment_ms=assessment_ms,
            session_questions=recent_questions,
            question_relation=readiness.relation,
            parent_question_ids=readiness.parent_question_ids,
            standalone_intent=readiness.standalone_intent,
        )
        return AutomaticCandidateResponse(
            **readiness.model_dump(mode="json"),
            assessment_ms=assessment_ms,
            run_id=accepted.run_id,
            created_at=accepted.created_at,
        )

    async def _enqueue_run(
        self,
        request: CreateRunRequest,
        *,
        supervisor_instruction: str | None = None,
        supervisor_reason: str = "",
        assessment_ms: int | None = None,
        session_questions: list[dict] | None = None,
        question_relation: str = "none",
        parent_question_ids: list[str] | None = None,
        standalone_intent: str = "",
    ) -> RunAccepted:
        run_id = str(uuid4())
        created_at = _now()
        await self._repository.create(run_id, created_at)
        initial_state = {
            "run_id": run_id,
            "source_text": request.text,
            "session_id": getattr(request, "session_id", None),
            "project_id": request.project_id,
            "project_name": request.project_name,
            "supervisor_model": request.supervisor_model.model_dump(mode="json"),
            "question_model": request.question_model.model_dump(mode="json"),
            "status": "generating" if supervisor_instruction else "supervising",
            "attempt": 0,
            "questions": [],
            "review_action": None,
            "review_feedback": "",
            "target_question_id": None,
            "retrieval_status": "not_started",
            "question_generation_ms": assessment_ms,
            "question_retrieval_ms": None,
            "response_return_ms": None,
            "error": None,
            "created_at": created_at,
            "updated_at": created_at,
            "supervisor_preassessed": supervisor_instruction is not None,
            "supervisor_instruction": supervisor_instruction or "",
            "supervisor_reason": supervisor_reason,
            "question_relation": question_relation,
            "parent_question_ids": parent_question_ids or [],
            "standalone_intent": standalone_intent,
            "session_questions": session_questions or [],
        }
        self._track(run_id, self._execute_initial_run(run_id, initial_state))
        return RunAccepted(run_id=run_id, created_at=created_at)

    async def _execute_initial_run(self, run_id: str, initial_state: dict) -> None:
        try:
            await self._graph.ainvoke(initial_state, self._config(run_id))
            response = await self.get_run(run_id)
            await self._repository.update(run_id, response.status, response.error)
            if self._question_memory is not None:
                self._question_memory.enqueue_snapshot(response)
        except Exception as exc:
            await self._repository.update(run_id, "failed", str(exc))

    async def enqueue_regeneration(
        self, run_id: str, decision: ReviewDecision
    ) -> RunAccepted:
        current = await self.get_run(run_id)
        if run_id in self._active_run_ids:
            raise InvalidRunStateError(f"run {run_id} is already being processed")
        if current.status != "awaiting_review":
            raise InvalidRunStateError(
                f"run {run_id} is {current.status}, not awaiting_review"
            )
        await self._repository.update(run_id, "generating")
        self._track(run_id, self._execute_review(run_id, decision))
        return RunAccepted(run_id=run_id, created_at=current.created_at)

    async def _execute_review(self, run_id: str, decision: ReviewDecision) -> None:
        try:
            await self.review_run(run_id, decision)
        except Exception as exc:
            await self._repository.update(run_id, "failed", str(exc))

    async def review_run(self, run_id: str, decision: ReviewDecision) -> RunResponse:
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            current = await self.get_run(run_id)
            if current.status != "awaiting_review":
                raise InvalidRunStateError(
                    f"run {run_id} is {current.status}, not awaiting_review"
                )
            try:
                await self._graph.ainvoke(
                    Command(resume=decision.model_dump(mode="json")),
                    self._config(run_id),
                )
                response = await self.get_run(run_id)
                await self._repository.update(run_id, response.status, response.error)
                if self._question_memory is not None:
                    self._question_memory.enqueue_snapshot(response)
                return response
            except Exception as exc:
                await self._repository.update(run_id, "failed", str(exc))
                raise

    async def get_run(self, run_id: str) -> RunResponse:
        registry_status = await self._repository.status(run_id)
        if registry_status is None:
            raise RunNotFoundError(run_id)
        snapshot = await self._graph.aget_state(self._config(run_id))
        if not snapshot.values:
            raise RunNotFoundError(run_id)
        response = RunResponse.model_validate(snapshot.values)
        status, error = registry_status
        if status == "failed":
            response.status = "failed"
            response.error = error
        return response

    async def get_run_status(self, run_id: str) -> RunStatusResponse:
        registry_status = await self._repository.status(run_id)
        if registry_status is None:
            raise RunNotFoundError(run_id)
        status, error = registry_status
        return RunStatusResponse(run_id=run_id, status=status, error=error)

    async def list_runs(self) -> list[RunResponse]:
        responses = []
        for run_id in await self._repository.list_ids():
            try:
                responses.append(await self.get_run(run_id))
            except RunNotFoundError:
                continue
        return responses

    async def clear_runs(self) -> int:
        await self._cancel_tasks()
        run_ids = await self._repository.list_ids(limit=None)
        for run_id in run_ids:
            await self._graph.checkpointer.adelete_thread(run_id)
        await self._repository.delete_all()
        if self._question_memory is not None:
            await self._question_memory.clear_all()
        self._locks.clear()
        return len(run_ids)

    async def end_session(self, session_id: str) -> None:
        if self._question_memory is not None:
            await self._question_memory.close_session(session_id)

    async def shutdown(self) -> None:
        await self._cancel_tasks()
        if self._question_memory is not None:
            await self._question_memory.shutdown()

    async def _cancel_tasks(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._active_run_ids.clear()

