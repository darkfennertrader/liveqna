from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from langgraph.types import Command

from .repository import RunRepository
from .schemas import (
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
    def __init__(self, *, graph, repository: RunRepository) -> None:
        self._graph = graph
        self._repository = repository
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
        run_id = str(uuid4())
        created_at = _now()
        await self._repository.create(run_id, created_at)
        initial_state = {
            "run_id": run_id,
            "source_text": request.text,
            "project_id": request.project_id,
            "project_name": request.project_name,
            "supervisor_model": request.supervisor_model.model_dump(mode="json"),
            "question_model": request.question_model.model_dump(mode="json"),
            "status": "supervising",
            "attempt": 0,
            "questions": [],
            "review_action": None,
            "review_feedback": "",
            "target_question_id": None,
            "retrieval_status": "not_started",
            "question_generation_ms": None,
            "question_retrieval_ms": None,
            "response_return_ms": None,
            "error": None,
            "created_at": created_at,
            "updated_at": created_at,
        }
        self._track(run_id, self._execute_initial_run(run_id, initial_state))
        return RunAccepted(run_id=run_id, created_at=created_at)

    async def _execute_initial_run(self, run_id: str, initial_state: dict) -> None:
        try:
            await self._graph.ainvoke(initial_state, self._config(run_id))
            response = await self.get_run(run_id)
            await self._repository.update(run_id, response.status)
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
                await self._repository.update(run_id, response.status)
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
        self._locks.clear()
        return len(run_ids)

    async def shutdown(self) -> None:
        await self._cancel_tasks()

    async def _cancel_tasks(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._active_run_ids.clear()

