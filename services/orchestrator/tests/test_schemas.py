import pytest
from pydantic import ValidationError

from app.providers.base import ModelConfigurationError
from app.providers.registry import create_provider_registry
from app.schemas import ModelSelection, QuestionCandidate, ReviewDecision


def test_approval_requires_at_least_one_question() -> None:
    with pytest.raises(ValidationError):
        ReviewDecision(action="approve", questions=[])


def test_rejection_allows_no_questions() -> None:
    decision = ReviewDecision(
        action="reject",
        question_id="q1",
        questions=[{"id": "q1", "question": "Question?"}],
    )
    assert decision.action == "reject"


def test_question_cannot_be_whitespace_only() -> None:
    with pytest.raises(ValidationError):
        QuestionCandidate(question="   ")


def test_legacy_priority_alias_is_not_exposed_as_a_service_tier() -> None:
    with pytest.raises(ValidationError):
        ModelSelection(
            provider="openai",
            model="gpt-6.1-sol",
            service_tier="priority",
        )


@pytest.mark.parametrize("model", ["gpt-6.1-sol", "gpt-6.1-sol-2026-10-01"])
def test_gpt_61_sol_rejects_none_reasoning(model: str) -> None:
    selection = ModelSelection(
        provider="openai",
        model=model,
        parameters={"reasoning_effort": "none"},
    )

    with pytest.raises(ModelConfigurationError, match="choose one of"):
        create_provider_registry().normalize_selection(selection)


def test_legacy_and_modular_parameters_cannot_conflict() -> None:
    with pytest.raises(ValidationError, match="conflicting values"):
        ModelSelection(
            provider="openai",
            model="gpt-6-luna",
            parameters={"reasoning_effort": "medium"},
            reasoning_effort="low",
        )


def test_models_that_support_none_reasoning_remain_selectable() -> None:
    selection = ModelSelection(
        provider="openai",
        model="gpt-6-sol",
        reasoning_effort="none",
    )

    assert selection.reasoning_effort == "none"

