from __future__ import annotations

import os
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .base import (
    ModelConfigurationError,
    ModelParameterConstraint,
    ParameterDescriptor,
    ParameterOption,
    ParameterValue,
    ProviderDescriptor,
)


ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]
ServiceTier = Literal["default", "fast", "ultrafast", "flex"]


class OpenAIParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    reasoning_effort: ReasoningEffort | None = None
    service_tier: ServiceTier | None = None


class OpenAIAdapter:
    name = "openai"

    _reasoning_options = [
        ParameterOption(value="none", label="None"),
        ParameterOption(value="low", label="Low"),
        ParameterOption(value="medium", label="Medium"),
        ParameterOption(value="high", label="High"),
        ParameterOption(value="xhigh", label="Extra high"),
        ParameterOption(value="max", label="Max"),
    ]
    _reasoning_without_none = ["low", "medium", "high", "xhigh", "max"]

    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            name=self.name,
            label="OpenAI",
            parameters=[
                ParameterDescriptor(
                    name="reasoning_effort",
                    label="Reasoning effort",
                    control="select",
                    options=self._reasoning_options,
                    help="Model-dependent reasoning budget.",
                ),
                ParameterDescriptor(
                    name="service_tier",
                    label="Service tier",
                    control="select",
                    options=[
                        ParameterOption(value="default", label="Default"),
                        ParameterOption(value="fast", label="Fast"),
                        ParameterOption(value="ultrafast", label="Ultrafast"),
                        ParameterOption(value="flex", label="Flex"),
                    ],
                    help="OpenAI request processing tier.",
                ),
                ParameterDescriptor(
                    name="temperature",
                    label="Temperature",
                    control="number",
                    minimum=0.0,
                    maximum=2.0,
                    step=0.1,
                    help="Leave blank to use the model default.",
                ),
            ],
            model_constraints=[
                ModelParameterConstraint(
                    model_family="gpt-6.1-sol",
                    allowed_values={
                        "reasoning_effort": self._reasoning_without_none
                    },
                    defaults={"reasoning_effort": "low"},
                ),
                ModelParameterConstraint(
                    model_family="gpt-6-astra",
                    allowed_values={
                        "reasoning_effort": self._reasoning_without_none
                    },
                    defaults={"reasoning_effort": "low"},
                ),
            ],
        )

    def normalize_parameters(
        self, model: str, parameters: dict[str, ParameterValue]
    ) -> dict[str, ParameterValue]:
        try:
            validated = OpenAIParameters.model_validate(parameters)
        except ValidationError as exc:
            raise ModelConfigurationError(str(exc)) from exc

        normalized = validated.model_dump(exclude_none=True)
        for constraint in self.descriptor().model_constraints:
            if not constraint.matches(model):
                continue
            for parameter, allowed in constraint.allowed_values.items():
                value = normalized.get(parameter)
                if value is not None and value not in allowed:
                    allowed_text = ", ".join(str(item) for item in allowed)
                    raise ModelConfigurationError(
                        f"{parameter} {value!r} is not supported by {model}; "
                        f"choose one of: {allowed_text}"
                    )
        return normalized

    def build(
        self, model: str, parameters: dict[str, ParameterValue]
    ) -> BaseChatModel:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ModelConfigurationError(
                "OPENAI_API_KEY is required when an OpenAI agent model is selected"
            )
        options: dict[str, Any] = {
            "model": model,
            "api_key": api_key,
            "timeout": float(os.environ.get("AGENT_MODEL_TIMEOUT_SECONDS", "30")),
            "max_retries": 1,
            "use_responses_api": True,
            **parameters,
        }
        return ChatOpenAI(**options)

    def with_structured_output(
        self, model: BaseChatModel, schema: type[BaseModel]
    ) -> Any:
        return model.with_structured_output(schema, method="json_schema")
