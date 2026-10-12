from __future__ import annotations

from typing import NotRequired, TypedDict


class OrchestrationState(TypedDict):
    run_id: str
    source_text: str
    supervisor_model: dict
    question_model: dict
    status: str
    attempt: int
    created_at: str
    updated_at: str
    supervisor_instruction: NotRequired[str]
    supervisor_reason: NotRequired[str]
    questions: NotRequired[list[dict]]
    review_action: NotRequired[str | None]
    review_feedback: NotRequired[str]
    target_question_id: NotRequired[str | None]
    retrieval_status: NotRequired[str]
    question_generation_ms: NotRequired[int | None]
    question_retrieval_ms: NotRequired[int | None]
    response_return_ms: NotRequired[int | None]
    error: NotRequired[str | None]

