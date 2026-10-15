from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .providers.base import ParameterValue, ProviderDescriptor


ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]
ServiceTier = Literal["default", "fast", "ultrafast", "flex"]
RunStatus = Literal[
    "supervising",
    "generating",
    "awaiting_review",
    "validated",
    "rejected",
    "failed",
]


class ModelSelection(BaseModel):
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    parameters: dict[str, ParameterValue] = Field(default_factory=dict)

    # Transitional fields keep persisted runs and older clients readable. New
    # clients send provider-specific values in ``parameters``.
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    reasoning_effort: ReasoningEffort | None = None
    service_tier: ServiceTier | None = None

    @model_validator(mode="before")
    @classmethod
    def merge_legacy_parameters(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        raw_parameters = data.get("parameters")
        if raw_parameters is None:
            raw_parameters = {}
        if not isinstance(raw_parameters, dict):
            return data
        parameters = dict(raw_parameters)
        for name in ("temperature", "reasoning_effort", "service_tier"):
            legacy_value = data.get(name)
            if legacy_value is None:
                continue
            if name in parameters and parameters[name] != legacy_value:
                raise ValueError(
                    f"conflicting values for legacy field and parameters.{name}"
                )
            parameters[name] = legacy_value
        data["parameters"] = parameters
        return data

    @field_validator("provider", "model")
    @classmethod
    def strip_identifier(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("parameters")
    @classmethod
    def validate_parameters(
        cls, value: dict[str, ParameterValue]
    ) -> dict[str, ParameterValue]:
        if len(value) > 32:
            raise ValueError("at most 32 model parameters are allowed")
        for name in value:
            if not name or len(name) > 100:
                raise ValueError("model parameter names must contain 1-100 characters")
        return value


class QuestionCandidate(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)
    source_excerpt: str = Field(default="", max_length=2_000)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        return value

    @field_validator("source_excerpt")
    @classmethod
    def strip_source_excerpt(cls, value: str) -> str:
        return value.strip()


class GeneratedQuestionSet(BaseModel):
    questions: list[QuestionCandidate] = Field(default_factory=list, max_length=20)


class QuestionDraft(QuestionCandidate):
    id: str = Field(min_length=1, max_length=100)
    review_status: Literal["pending", "validated", "rejected"] = "pending"


class SupervisorDecision(BaseModel):
    route: Literal["question_generator", "human_review"]
    instruction: str = Field(min_length=1, max_length=2_000)
    reason: str = Field(default="", max_length=1_000)


class CreateRunRequest(BaseModel):
    text: str = Field(min_length=1, max_length=30_000)
    project_id: str = Field(min_length=1, max_length=100)
    project_name: str = Field(min_length=1, max_length=200)
    supervisor_model: ModelSelection
    question_model: ModelSelection

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("captured text must not be blank")
        return value

    @field_validator("project_id", "project_name")
    @classmethod
    def strip_project(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("project must not be blank")
        return value


class ReviewDecision(BaseModel):
    action: Literal["approve", "reject", "regenerate"]
    question_id: str = Field(min_length=1, max_length=100)
    questions: list[QuestionDraft] = Field(default_factory=list, max_length=20)
    feedback: str = Field(default="", max_length=2_000)

    @field_validator("feedback")
    @classmethod
    def strip_feedback(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def require_target_question(self) -> "ReviewDecision":
        if not any(item.id == self.question_id for item in self.questions):
            raise ValueError("question_id must identify one of the submitted questions")
        return self


class RunResponse(BaseModel):
    run_id: str
    source_text: str
    project_id: str | None = None
    project_name: str | None = None
    status: RunStatus
    questions: list[QuestionDraft] = Field(default_factory=list)
    attempt: int = 0
    supervisor_model: ModelSelection
    question_model: ModelSelection
    supervisor_reason: str = ""
    retrieval_status: Literal["not_started", "ready"] = "not_started"
    question_generation_ms: int | None = Field(default=None, ge=0)
    question_retrieval_ms: int | None = Field(default=None, ge=0)
    response_return_ms: int | None = Field(default=None, ge=0)
    error: str | None = None
    created_at: str
    updated_at: str


class RunAccepted(BaseModel):
    run_id: str
    status: Literal["generating"] = "generating"
    accepted: Literal[True] = True
    created_at: str


class RunStatusResponse(BaseModel):
    run_id: str
    status: RunStatus
    error: str | None = None


class ModelOptionsResponse(BaseModel):
    providers: list[str]
    provider_catalog: list[ProviderDescriptor]
    supervisor_default: ModelSelection
    question_default: ModelSelection

