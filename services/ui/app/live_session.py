from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any
from uuid import uuid4


AssessCandidate = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
EmitEvent = Callable[[dict[str, Any]], Awaitable[None]]
EndSessionMemory = Callable[[str], Awaitable[None]]


class LiveSessionStateError(RuntimeError):
    pass


def _positive_env(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class LiveSessionConfig:
    silence_ms: int = 500
    max_wait_ms: int = 18_000
    max_words: int = 140
    finalize_grace_ms: int = 3_000

    def __post_init__(self) -> None:
        for name, value in (
            ("silence_ms", self.silence_ms),
            ("max_wait_ms", self.max_wait_ms),
            ("max_words", self.max_words),
            ("finalize_grace_ms", self.finalize_grace_ms),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")

    @classmethod
    def from_env(cls) -> "LiveSessionConfig":
        return cls(
            silence_ms=_positive_env("LIVE_SESSION_SILENCE_MS", 500),
            max_wait_ms=_positive_env("LIVE_SESSION_MAX_WAIT_MS", 18_000),
            max_words=_positive_env("LIVE_SESSION_MAX_WORDS", 140),
            finalize_grace_ms=_positive_env(
                "LIVE_SESSION_FINALIZE_GRACE_MS", 3_000
            ),
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "silence_ms": self.silence_ms,
            "max_wait_ms": self.max_wait_ms,
            "max_words": self.max_words,
            "finalize_grace_ms": self.finalize_grace_ms,
        }


class LiveSessionManager:
    """Build automatic transcript candidates without blocking the STT relay."""

    def __init__(
        self,
        *,
        config: LiveSessionConfig,
        assess_candidate: AssessCandidate,
        emit_event: EmitEvent,
        end_session_memory: EndSessionMemory | None = None,
    ) -> None:
        self.config = config
        self._assess_candidate = assess_candidate
        self._emit_event = emit_event
        self._end_session_memory = end_session_memory
        self._lock = asyncio.Lock()
        self._state = "idle"
        self._session_id: str | None = None
        self._context: dict[str, Any] | None = None
        self._sequence = 0
        self._final_text: list[str] = []
        self._final_segment_ids: set[str] = set()
        self._latest_partial: tuple[str | None, str] | None = None
        self._buffer_started_at: float | None = None
        self._last_speech_activity_at: float | None = None
        self._waiting_for_more = False
        self._assessing = False
        self._silence_task: asyncio.Task | None = None
        self._max_wait_task: asyncio.Task | None = None
        self._finalize_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()

    @property
    def state(self) -> str:
        return self._state

    async def start(self, context: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            if self._state != "idle":
                raise LiveSessionStateError(
                    f"the previous Live Q&A session is {self._state}"
                )
            self._reset_buffer_locked()
            self._state = "active"
            self._session_id = str(uuid4())
            self._context = dict(context)
            self._sequence = 0
            session_id = self._session_id
        payload = {
            "kind": "live_session_status",
            "status": "active",
            "session_id": session_id,
            "config": self.config.as_dict(),
        }
        await self._emit_event(payload)
        return payload

    async def ingest(self, event: dict[str, Any]) -> None:
        text = str(event.get("text") or "").strip()
        if not text or event.get("type") not in {"partial", "final"}:
            return
        now = monotonic()
        async with self._lock:
            if self._state not in {"active", "ending"}:
                return
            segment_id = event.get("segment_id")
            segment_key = str(segment_id) if segment_id is not None else None
            if self._buffer_started_at is None:
                self._buffer_started_at = now

            if event["type"] == "partial":
                self._latest_partial = (segment_key, text)
                self._last_speech_activity_at = now
            else:
                if segment_key is not None and segment_key in self._final_segment_ids:
                    return
                self._final_text.append(text)
                if segment_key is not None:
                    self._final_segment_ids.add(segment_key)
                if (
                    self._latest_partial is None
                    or segment_key is None
                    or self._latest_partial[0] == segment_key
                ):
                    self._latest_partial = None
                self._last_speech_activity_at = now

            if self._waiting_for_more:
                self._waiting_for_more = False
            if self._state == "active" and not self._assessing:
                self._schedule_boundaries_locked(now)

    async def end(self) -> dict[str, Any]:
        async with self._lock:
            if self._state == "idle":
                return {
                    "kind": "live_session_status",
                    "status": "idle",
                    "session_id": None,
                }
            if self._state == "ending":
                return {
                    "kind": "live_session_status",
                    "status": "ending",
                    "session_id": self._session_id,
                }
            self._state = "ending"
            self._cancel_boundary_tasks_locked()
            session_id = self._session_id
            self._finalize_task = self._spawn(self._finalize(session_id))
        payload = {
            "kind": "live_session_status",
            "status": "ending",
            "session_id": session_id,
        }
        await self._emit_event(payload)
        return payload

    async def shutdown(self) -> None:
        async with self._lock:
            tasks = list(self._tasks)
            for task in tasks:
                task.cancel()
            self._state = "idle"
            self._session_id = None
            self._context = None
            self._reset_buffer_locked()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _spawn(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _schedule_boundaries_locked(self, now: float) -> None:
        if (
            self._waiting_for_more
            or self._assessing
            or not self._combined_text_locked(include_partial=True)
        ):
            return
        self._cancel_task(self._silence_task)
        last_activity = self._last_speech_activity_at or now
        silence_remaining = max(
            0.0, self.config.silence_ms / 1000 - (now - last_activity)
        )
        session_id = self._session_id
        self._silence_task = self._spawn(
            self._dispatch_after(silence_remaining, session_id, "silence", False)
        )

        self._schedule_max_wait_locked(now)

        if self._word_count_locked() >= self.config.max_words:
            self._spawn(self._dispatch(session_id, "max_words", True))

    def _schedule_max_wait_locked(self, now: float) -> None:
        if self._max_wait_task is not None and not self._max_wait_task.done():
            return
        started = self._buffer_started_at or now
        wait_remaining = max(
            0.0, self.config.max_wait_ms / 1000 - (now - started)
        )
        self._max_wait_task = self._spawn(
            self._dispatch_after(
                wait_remaining, self._session_id, "max_wait", True
            )
        )

    async def _dispatch_after(
        self, delay: float, session_id: str | None, trigger: str, force: bool
    ) -> None:
        await asyncio.sleep(delay)
        await self._dispatch(session_id, trigger, force)

    async def _dispatch(
        self, session_id: str | None, trigger: str, force: bool
    ) -> None:
        async with self._lock:
            if session_id is None or session_id != self._session_id:
                return
            if self._assessing or (self._waiting_for_more and not force):
                return
            if force:
                self._waiting_for_more = False
            text = self._combined_text_locked(include_partial=True)
            if not text or self._context is None:
                return
            self._assessing = True
            self._sequence += 1
            sequence = self._sequence
            candidate_started_at = self._buffer_started_at
            context = dict(self._context)
            if self._latest_partial is not None and self._latest_partial[0] is not None:
                self._final_segment_ids.add(self._latest_partial[0])
            self._clear_candidate_locked()

        await self._emit_event(
            {
                "kind": "session_assessment_started",
                "session_id": session_id,
                "sequence": sequence,
                "source_text": text,
                "trigger": trigger,
            }
        )
        try:
            result = await self._assess_candidate(
                {
                    "text": text,
                    "session_id": session_id,
                    "project_id": context["project_id"],
                    "project_name": context["project_name"],
                    "supervisor_model": context["supervisor_model"],
                    "question_model": context["question_model"],
                    "force_decision": force,
                }
            )
        except Exception as exc:
            async with self._lock:
                if session_id == self._session_id:
                    self._final_text.insert(0, text)
                    self._buffer_started_at = candidate_started_at or monotonic()
                    self._assessing = False
                    self._waiting_for_more = True
            await self._emit_event(
                {
                    "kind": "session_candidate_failed",
                    "session_id": session_id,
                    "sequence": sequence,
                    "source_text": text,
                    "error": str(exc),
                }
            )
            return

        decision = result.get("decision", "ignore")
        async with self._lock:
            if session_id != self._session_id:
                return
            self._assessing = False
            if decision == "wait":
                self._final_text.insert(0, text)
                self._buffer_started_at = candidate_started_at or monotonic()
                self._waiting_for_more = self._state == "active"
                if self._waiting_for_more:
                    self._schedule_max_wait_locked(monotonic())
            elif self._state == "active" and self._combined_text_locked():
                self._schedule_boundaries_locked(monotonic())

        await self._emit_event(
            {
                "kind": "session_candidate",
                "session_id": session_id,
                "sequence": sequence,
                "source_text": text,
                "trigger": trigger,
                **result,
            }
        )

    async def _finalize(self, session_id: str | None) -> None:
        await asyncio.sleep(self.config.finalize_grace_ms / 1000)
        while True:
            async with self._lock:
                if session_id != self._session_id:
                    return
                assessing = self._assessing
            if not assessing:
                break
            await asyncio.sleep(0.05)

        async with self._lock:
            if session_id != self._session_id:
                return
            self._waiting_for_more = False
        await self._dispatch(session_id, "session_end", True)

        while True:
            async with self._lock:
                if session_id != self._session_id:
                    return
                assessing = self._assessing
            if not assessing:
                break
            await asyncio.sleep(0.05)

        if session_id is not None and self._end_session_memory is not None:
            try:
                await self._end_session_memory(session_id)
            except Exception as exc:
                await self._emit_event(
                    {
                        "kind": "session_memory_cleanup_failed",
                        "session_id": session_id,
                        "error": str(exc),
                    }
                )

        async with self._lock:
            if session_id != self._session_id:
                return
            self._state = "idle"
            self._session_id = None
            self._context = None
            self._reset_buffer_locked()
        await self._emit_event(
            {
                "kind": "live_session_status",
                "status": "ended",
                "session_id": session_id,
            }
        )

    def _combined_text_locked(self, include_partial: bool = False) -> str:
        parts = list(self._final_text)
        if include_partial and self._latest_partial is not None:
            partial = self._latest_partial[1]
            if not parts or parts[-1] != partial:
                parts.append(partial)
        return " ".join(part.strip() for part in parts if part.strip()).strip()

    def _word_count_locked(self) -> int:
        return len(re.findall(r"\S+", self._combined_text_locked(include_partial=True)))

    def _clear_candidate_locked(self) -> None:
        self._cancel_boundary_tasks_locked()
        self._final_text = []
        self._latest_partial = None
        self._buffer_started_at = None
        self._last_speech_activity_at = None

    def _reset_buffer_locked(self) -> None:
        self._cancel_boundary_tasks_locked()
        self._final_text = []
        self._final_segment_ids = set()
        self._latest_partial = None
        self._buffer_started_at = None
        self._last_speech_activity_at = None
        self._waiting_for_more = False
        self._assessing = False

    def _cancel_boundary_tasks_locked(self) -> None:
        self._cancel_task(self._silence_task)
        self._cancel_task(self._max_wait_task)
        self._silence_task = None
        self._max_wait_task = None

    @staticmethod
    def _cancel_task(task: asyncio.Task | None) -> None:
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
