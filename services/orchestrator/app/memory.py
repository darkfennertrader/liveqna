from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from langgraph.store.base import BaseStore

from .schemas import RunResponse


logger = logging.getLogger("orchestrator.memory")


class SessionQuestionMemory:
    """Ordered, non-blocking session question writes backed by LangGraph Store."""

    prompt_char_budget = 12_000

    def __init__(self, store: BaseStore, *, max_questions: int = 30) -> None:
        if max_questions <= 0:
            raise ValueError("max_questions must be greater than zero")
        self.store = store
        self.max_questions = max_questions
        self._queues: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._closed_sessions: set[str] = set()
        self._known_sessions: set[str] = set()

    @staticmethod
    def _namespace(session_id: str) -> tuple[str, ...]:
        return ("live_session", session_id, "questions")

    def enqueue_snapshot(self, run: RunResponse) -> None:
        """Queue a run snapshot without waiting for RAM-store writes."""
        session_id = run.session_id
        if not session_id or session_id in self._closed_sessions:
            return
        queue = self._ensure_worker(session_id)
        queue.put_nowait(run.model_dump(mode="json"))

    async def recent_questions(self, session_id: str) -> list[dict[str, Any]]:
        """Return an ordered snapshot after all earlier writes are visible."""
        if session_id in self._closed_sessions:
            return []
        queue = self._queues.get(session_id)
        if queue is not None:
            await queue.join()
        items = await self.store.asearch(
            self._namespace(session_id), limit=max(100, self.max_questions * 4)
        )
        records = [dict(item.value) for item in items]
        records = [
            record
            for record in records
            if record.get("review_status") != "rejected"
        ]
        records.sort(
            key=lambda record: (
                str(record.get("updated_at", "")),
                str(record.get("memory_id", "")),
            )
        )
        selected: list[dict[str, Any]] = []
        remaining = self.prompt_char_budget
        for record in reversed(records[-self.max_questions :]):
            compact = {
                "memory_id": str(record.get("memory_id", "")),
                "question": str(record.get("question", ""))[:1_000],
                "review_status": str(record.get("review_status", "pending")),
                "relation": str(record.get("relation", "new")),
                "parent_question_ids": list(
                    record.get("parent_question_ids", [])
                ),
            }
            cost = len(compact["question"]) + len(compact["memory_id"]) + 64
            if selected and cost > remaining:
                break
            selected.append(compact)
            remaining -= cost
        selected.reverse()
        return selected

    async def close_session(self, session_id: str) -> None:
        """Stop accepting late writes, flush queued work, and erase the session."""
        if not session_id:
            return
        self._closed_sessions.add(session_id)
        queue = self._queues.get(session_id)
        if queue is not None:
            await queue.join()
            queue.put_nowait(None)
            await queue.join()
        worker = self._workers.pop(session_id, None)
        if worker is not None:
            await asyncio.gather(worker, return_exceptions=True)
        self._queues.pop(session_id, None)
        await self._delete_namespace(session_id)

    async def clear_all(self) -> None:
        for session_id in list(self._known_sessions):
            await self.close_session(session_id)

    async def shutdown(self) -> None:
        await self.clear_all()

    def _ensure_worker(
        self, session_id: str
    ) -> asyncio.Queue[dict[str, Any] | None]:
        queue = self._queues.get(session_id)
        if queue is None:
            queue = asyncio.Queue()
            self._queues[session_id] = queue
            self._known_sessions.add(session_id)
            self._workers[session_id] = asyncio.create_task(
                self._run_worker(session_id, queue)
            )
        return queue

    async def _run_worker(
        self,
        session_id: str,
        queue: asyncio.Queue[dict[str, Any] | None],
    ) -> None:
        while True:
            payload = await queue.get()
            try:
                if payload is None:
                    return
                await self._write_snapshot(session_id, payload)
            except Exception:
                logger.exception(
                    "failed to update question memory for session %s", session_id
                )
            finally:
                queue.task_done()

    async def _write_snapshot(
        self, session_id: str, run: Mapping[str, Any]
    ) -> None:
        namespace = self._namespace(session_id)
        run_id = str(run["run_id"])
        for question in run.get("questions", []):
            question_id = str(question["id"])
            memory_id = f"{run_id}:{question_id}"
            await self.store.aput(
                namespace,
                memory_id,
                {
                    "memory_id": memory_id,
                    "run_id": run_id,
                    "question_id": question_id,
                    "question": str(question.get("question", "")),
                    "source_excerpt": str(question.get("source_excerpt", "")),
                    "review_status": str(
                        question.get("review_status", "pending")
                    ),
                    "relation": str(run.get("question_relation", "new")),
                    "parent_question_ids": list(
                        run.get("parent_question_ids", [])
                    ),
                    "project_id": run.get("project_id"),
                    "updated_at": str(run.get("updated_at", "")),
                },
                index=False,
            )
        await self._trim(namespace)

    async def _trim(self, namespace: tuple[str, ...]) -> None:
        items = await self.store.asearch(
            namespace, limit=max(100, self.max_questions * 4)
        )
        if len(items) <= self.max_questions:
            return
        items.sort(key=lambda item: (item.updated_at, item.key))
        for item in items[: -self.max_questions]:
            await self.store.adelete(namespace, item.key)

    async def _delete_namespace(self, session_id: str) -> None:
        namespace = self._namespace(session_id)
        items = await self.store.asearch(namespace, limit=1_000)
        for item in items:
            await self.store.adelete(namespace, item.key)
