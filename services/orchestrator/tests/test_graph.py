from __future__ import annotations

from collections import deque

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.graph import build_graph
from app.schemas import (
    GeneratedQuestionSet,
    QuestionCandidate,
    SupervisorDecision,
)


class FakeSupervisor:
    def __init__(self) -> None:
        self.calls = 0

    async def delegate(self, **kwargs) -> SupervisorDecision:
        self.calls += 1
        return SupervisorDecision(
            route="question_generator",
            instruction="Extract retrieval-ready questions.",
            reason="The input contains an information request.",
        )


class FakeQuestionGenerator:
    def __init__(self, batches: list[list[str]]) -> None:
        self.batches = deque(batches)

    async def generate(self, **kwargs) -> GeneratedQuestionSet:
        return GeneratedQuestionSet(
            questions=[
                QuestionCandidate(question=text, source_excerpt="source")
                for text in self.batches.popleft()
            ]
        )


def initial_state() -> dict:
    return {
        "run_id": "run-1",
        "source_text": "How did revenue change, and what caused the change?",
        "supervisor_model": {"provider": "openai", "model": "test", "temperature": 0},
        "question_model": {"provider": "openai", "model": "test", "temperature": 0},
        "status": "supervising",
        "attempt": 0,
        "questions": [],
        "review_action": None,
        "review_feedback": "",
        "retrieval_status": "not_started",
        "error": None,
        "created_at": "2026-10-10T00:00:00+00:00",
        "updated_at": "2026-10-10T00:00:00+00:00",
    }


def config() -> dict:
    return {"configurable": {"thread_id": "run-1"}}


@pytest.mark.asyncio
async def test_generates_multiple_questions_and_pauses_for_review() -> None:
    graph = build_graph(
        supervisor=FakeSupervisor(),
        question_generator=FakeQuestionGenerator(
            [["How did revenue change?", "What caused the change?"]]
        ),
        checkpointer=InMemorySaver(),
    )

    result = await graph.ainvoke(initial_state(), config())

    assert result["status"] == "awaiting_review"
    assert [item["question"] for item in result["questions"]] == [
        "How did revenue change?",
        "What caused the change?",
    ]
    assert result["question_generation_ms"] >= 0
    assert result["__interrupt__"]


@pytest.mark.asyncio
async def test_empty_generation_is_ignored_without_human_review() -> None:
    graph = build_graph(
        supervisor=FakeSupervisor(),
        question_generator=FakeQuestionGenerator([[]]),
        checkpointer=InMemorySaver(),
    )

    result = await graph.ainvoke(initial_state(), config())

    assert result["status"] == "ignored"
    assert result["questions"] == []
    assert "__interrupt__" not in result


@pytest.mark.asyncio
async def test_preassessed_run_skips_duplicate_supervisor_call() -> None:
    supervisor = FakeSupervisor()
    state = initial_state()
    state.update(
        {
            "supervisor_preassessed": True,
            "supervisor_instruction": "Use the existing readiness instruction.",
            "supervisor_reason": "Already assessed.",
            "question_generation_ms": 12,
        }
    )
    graph = build_graph(
        supervisor=supervisor,
        question_generator=FakeQuestionGenerator([["Ready question?"]]),
        checkpointer=InMemorySaver(),
    )

    result = await graph.ainvoke(state, config())

    assert supervisor.calls == 0
    assert result["status"] == "awaiting_review"
    assert result["question_generation_ms"] >= 12


@pytest.mark.asyncio
async def test_approval_keeps_human_edits_and_finishes() -> None:
    graph = build_graph(
        supervisor=FakeSupervisor(),
        question_generator=FakeQuestionGenerator([["Original question?"]]),
        checkpointer=InMemorySaver(),
    )
    await graph.ainvoke(initial_state(), config())

    result = await graph.ainvoke(
        Command(
            resume={
                "action": "approve",
                "question_id": "q1",
                "questions": [
                    {
                        "id": "q1",
                        "question": "Human-edited question?",
                        "source_excerpt": "source",
                    }
                ],
                "feedback": "",
            }
        ),
        config(),
    )

    assert result["status"] == "validated"
    assert result["retrieval_status"] == "ready"
    assert result["questions"][0]["question"] == "Human-edited question?"


@pytest.mark.asyncio
async def test_rejection_finishes_without_approved_questions() -> None:
    graph = build_graph(
        supervisor=FakeSupervisor(),
        question_generator=FakeQuestionGenerator([["Question?"]]),
        checkpointer=InMemorySaver(),
    )
    await graph.ainvoke(initial_state(), config())

    result = await graph.ainvoke(
        Command(
            resume={
                "action": "reject",
                "question_id": "q1",
                "questions": [
                    {
                        "id": "q1",
                        "question": "Question?",
                        "source_excerpt": "source",
                    }
                ],
                "feedback": "",
            }
        ),
        config(),
    )

    assert result["status"] == "rejected"


@pytest.mark.asyncio
async def test_regeneration_returns_to_human_review_with_new_questions() -> None:
    supervisor = FakeSupervisor()
    graph = build_graph(
        supervisor=supervisor,
        question_generator=FakeQuestionGenerator(
            [["First draft?"], ["Regenerated draft?"]]
        ),
        checkpointer=InMemorySaver(),
    )
    await graph.ainvoke(initial_state(), config())

    result = await graph.ainvoke(
        Command(
            resume={
                "action": "regenerate",
                "question_id": "q1",
                "questions": [
                    {
                        "id": "q1",
                        "question": "First draft?",
                        "source_excerpt": "source",
                    }
                ],
                "feedback": "Make it more specific.",
            }
        ),
        config(),
    )

    assert result["status"] == "awaiting_review"
    assert result["attempt"] == 1
    assert result["questions"][0]["question"] == "Regenerated draft?"
    assert result["__interrupt__"]
    assert supervisor.calls == 2


@pytest.mark.asyncio
async def test_questions_are_validated_independently() -> None:
    graph = build_graph(
        supervisor=FakeSupervisor(),
        question_generator=FakeQuestionGenerator([["First?", "Second?"]]),
        checkpointer=InMemorySaver(),
    )
    generated = await graph.ainvoke(initial_state(), config())

    first_review = await graph.ainvoke(
        Command(
            resume={
                "action": "approve",
                "question_id": "q1",
                "questions": generated["questions"],
                "feedback": "",
            }
        ),
        config(),
    )

    assert first_review["status"] == "awaiting_review"
    assert first_review["questions"][0]["review_status"] == "validated"
    assert first_review["questions"][1]["review_status"] == "pending"
    assert first_review["__interrupt__"]

    completed = await graph.ainvoke(
        Command(
            resume={
                "action": "approve",
                "question_id": "q2",
                "questions": first_review["questions"],
                "feedback": "",
            }
        ),
        config(),
    )

    assert completed["status"] == "validated"
    assert all(
        item["review_status"] == "validated" for item in completed["questions"]
    )

