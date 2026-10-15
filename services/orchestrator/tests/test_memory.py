from __future__ import annotations

import pytest
from langgraph.store.memory import InMemoryStore

from app.memory import SessionQuestionMemory
from app.schemas import ModelSelection, RunResponse


def run_snapshot(
    *,
    question: str,
    review_status: str = "pending",
    updated_at: str = "2026-10-15T10:00:00+00:00",
) -> RunResponse:
    selection = ModelSelection(provider="openai", model="test")
    return RunResponse(
        run_id="run-1",
        session_id="session-1",
        source_text="What changed?",
        project_id="project-1",
        project_name="Client A",
        status="awaiting_review",
        questions=[
            {
                "id": "q1",
                "question": question,
                "source_excerpt": "changed",
                "review_status": review_status,
            }
        ],
        supervisor_model=selection,
        question_model=selection,
        created_at="2026-10-15T09:59:00+00:00",
        updated_at=updated_at,
    )


@pytest.mark.asyncio
async def test_async_snapshot_updates_are_visible_before_next_read() -> None:
    memory = SessionQuestionMemory(InMemoryStore())
    memory.enqueue_snapshot(run_snapshot(question="Initial question?"))
    memory.enqueue_snapshot(
        run_snapshot(
            question="Human-edited question?",
            review_status="validated",
            updated_at="2026-10-15T10:01:00+00:00",
        )
    )

    recent = await memory.recent_questions("session-1")

    assert len(recent) == 1
    assert recent[0]["memory_id"] == "run-1:q1"
    assert recent[0]["question"] == "Human-edited question?"
    assert recent[0]["review_status"] == "validated"
    await memory.shutdown()


@pytest.mark.asyncio
async def test_rejected_questions_are_excluded_and_closed_session_stays_empty() -> None:
    memory = SessionQuestionMemory(InMemoryStore())
    rejected = run_snapshot(question="Rejected?", review_status="rejected")
    memory.enqueue_snapshot(rejected)

    assert await memory.recent_questions("session-1") == []

    await memory.close_session("session-1")
    memory.enqueue_snapshot(run_snapshot(question="Late write?"))

    assert await memory.recent_questions("session-1") == []
    await memory.shutdown()
