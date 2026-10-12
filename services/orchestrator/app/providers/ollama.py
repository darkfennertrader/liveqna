from __future__ import annotations

import os
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_ollama import ChatOllama
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .base import (
    ModelConfigurationError,
    ParameterDescriptor,
    ParameterValue,
    ProviderDescriptor,
)


class OllamaParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class OllamaAdapter:
    name = "ollama"

    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            name=self.name,
            label="Ollama (local)",
            parameters=[
                ParameterDescriptor(
                    name="temperature",
                    label="Temperature",
                    control="number",
                    minimum=0.0,
                    maximum=2.0,
                    step=0.1,
                    help="Leave blank to use the model default.",
                )
            ],
        )

    def normalize_parameters(
        self, model: str, parameters: dict[str, ParameterValue]
    ) -> dict[str, ParameterValue]:
        del model
        try:
            validated = OllamaParameters.model_validate(parameters)
        except ValidationError as exc:
            raise ModelConfigurationError(str(exc)) from exc
        return validated.model_dump(exclude_none=True)

    def build(
        self, model: str, parameters: dict[str, ParameterValue]
    ) -> BaseChatModel:
        options: dict[str, Any] = {
            "model": model,
            "base_url": os.environ.get(
                "OLLAMA_BASE_URL", "http://host.docker.internal:11434"
            ),
            **parameters,
        }
        return ChatOllama(**options)

    def with_structured_output(
        self, model: BaseChatModel, schema: type[BaseModel]
    ) -> Any:
        return model.with_structured_output(schema)
