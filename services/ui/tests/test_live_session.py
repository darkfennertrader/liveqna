from __future__ import annotations

import asyncio
from collections import deque

import pytest

from app.live_session import LiveSessionConfig, LiveSessionManager


class Harness:
    def __init__(self, decisions: list[str]) -> None:
        self.decisions = deque(decisions)
        self.requests: list[dict] = []
        self.events: list[dict] = []
        self.ended_sessions: list[str] = []

    async def assess(self, payload: dict) -> dict:
        self.requests.append(payload)
        decision = self.decisions.popleft()
        return {
            "decision": decision,
            "instruction": "Generate supported questions.",
            "reason": f"test {decision}",
            "confidence": 0.9,
            "assessment_ms": 1,
            "run_id": "run-1" if decision == "ready" else None,
            "created_at": "2026-10-15T00:00:00+00:00"
            if decision == "ready"
            else None,
        }

    async def emit(self, event: dict) -> None:
        self.events.append(event)

    async def end_session(self, session_id: str) -> None:
        self.ended_sessions.append(session_id)


def context() -> dict:
    selection = {"provider": "openai", "model": "test", "parameters": {}}
    return {
        "project_id": "project-1",
        "project_name": "Client A",
        "supervisor_model": selection,
        "question_model": selection,
    }


async def wait_for_requests(harness: Harness, count: int) -> None:
    async with asyncio.timeout(1):
        while len(harness.requests) < count:
            await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_silence_dispatches_final_transcript_asynchronously() -> None:
    harness = Harness(["ready"])
    manager = LiveSessionManager(
        config=LiveSessionConfig(
            silence_ms=20, max_wait_ms=500, max_words=150, finalize_grace_ms=20
        ),
        assess_candidate=harness.assess,
        emit_event=harness.emit,
    )
    await manager.start(context())

    await manager.ingest(
        {"type": "final", "text": "What changed?", "segment_id": 1}
    )
    await wait_for_requests(harness, 1)

    assert harness.requests[0]["text"] == "What changed?"
    assert harness.requests[0]["session_id"]
    assert harness.requests[0]["force_decision"] is False
    assert any(event["kind"] == "session_candidate" for event in harness.events)
    await manager.shutdown()


@pytest.mark.asyncio
async def test_wait_keeps_text_until_more_context_arrives() -> None:
    harness = Harness(["wait", "ready"])
    manager = LiveSessionManager(
        config=LiveSessionConfig(
            silence_ms=20, max_wait_ms=500, max_words=150, finalize_grace_ms=20
        ),
        assess_candidate=harness.assess,
        emit_event=harness.emit,
    )
    await manager.start(context())

    await manager.ingest(
        {"type": "final", "text": "Could you explain", "segment_id": 1}
    )
    await wait_for_requests(harness, 1)
    await asyncio.sleep(0.04)
    assert len(harness.requests) == 1

    await manager.ingest(
        {"type": "final", "text": "why the cost increased?", "segment_id": 2}
    )
    await wait_for_requests(harness, 2)

    assert harness.requests[1]["text"] == (
        "Could you explain why the cost increased?"
    )
    await manager.shutdown()


@pytest.mark.asyncio
async def test_wait_is_forced_at_original_max_wait_deadline() -> None:
    harness = Harness(["wait", "ignore"])
    manager = LiveSessionManager(
        config=LiveSessionConfig(
            silence_ms=10, max_wait_ms=60, max_words=150, finalize_grace_ms=20
        ),
        assess_candidate=harness.assess,
        emit_event=harness.emit,
    )
    await manager.start(context())

    await manager.ingest(
        {"type": "final", "text": "Could you explain", "segment_id": 1}
    )
    await wait_for_requests(harness, 2)

    assert harness.requests[0]["force_decision"] is False
    assert harness.requests[1]["force_decision"] is True
    assert harness.requests[1]["text"] == "Could you explain"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_max_words_forces_readiness_check() -> None:
    harness = Harness(["ready"])
    manager = LiveSessionManager(
        config=LiveSessionConfig(
            silence_ms=500, max_wait_ms=500, max_words=3, finalize_grace_ms=20
        ),
        assess_candidate=harness.assess,
        emit_event=harness.emit,
    )
    await manager.start(context())

    await manager.ingest(
        {"type": "final", "text": "one two three", "segment_id": 1}
    )
    await wait_for_requests(harness, 1)

    assert harness.requests[0]["force_decision"] is True
    await manager.shutdown()


@pytest.mark.asyncio
async def test_end_session_flushes_latest_partial_with_force_decision() -> None:
    harness = Harness(["ignore"])
    manager = LiveSessionManager(
        config=LiveSessionConfig(
            silence_ms=500, max_wait_ms=500, max_words=150, finalize_grace_ms=20
        ),
        assess_candidate=harness.assess,
        emit_event=harness.emit,
        end_session_memory=harness.end_session,
    )
    await manager.start(context())
    await manager.ingest(
        {"type": "partial", "text": "unfinished final request", "segment_id": 8}
    )

    response = await manager.end()
    await wait_for_requests(harness, 1)

    assert response["status"] == "ending"
    assert harness.requests[0]["text"] == "unfinished final request"
    assert harness.requests[0]["force_decision"] is True
    async with asyncio.timeout(1):
        while manager.state != "idle":
            await asyncio.sleep(0.005)
    assert harness.ended_sessions == [harness.requests[0]["session_id"]]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_end_forces_again_when_inflight_assessment_returns_wait() -> None:
    harness = Harness(["wait", "ignore"])
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def assess(payload: dict) -> dict:
        if not harness.requests:
            first_started.set()
            await release_first.wait()
        return await harness.assess(payload)

    manager = LiveSessionManager(
        config=LiveSessionConfig(
            silence_ms=10, max_wait_ms=500, max_words=150, finalize_grace_ms=10
        ),
        assess_candidate=assess,
        emit_event=harness.emit,
    )
    await manager.start(context())
    await manager.ingest(
        {"type": "final", "text": "Could you explain", "segment_id": 1}
    )
    await asyncio.wait_for(first_started.wait(), timeout=1)

    await manager.end()
    release_first.set()
    await wait_for_requests(harness, 2)

    assert harness.requests[0]["force_decision"] is False
    assert harness.requests[1]["text"] == "Could you explain"
    assert harness.requests[1]["force_decision"] is True
    await manager.shutdown()
