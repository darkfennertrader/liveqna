from __future__ import annotations

from typing import Any, Literal, Protocol, TypeAlias, runtime_checkable

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field


ParameterValue: TypeAlias = str | bool | int | float | None
ParameterControl = Literal["select", "number", "text", "checkbox"]


class ModelConfigurationError(ValueError):
    """Raised when a provider or one of its parameters is invalid."""


class ParameterOption(BaseModel):
    value: ParameterValue
    label: str


class ParameterDescriptor(BaseModel):
    name: str
    label: str
    control: ParameterControl
    default: ParameterValue = None
    nullable: bool = True
    options: list[ParameterOption] = Field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    help: str = ""


class ModelParameterConstraint(BaseModel):
    """Parameter overrides for a model family and its dated snapshots."""

    model_family: str
    allowed_values: dict[str, list[ParameterValue]] = Field(default_factory=dict)
    defaults: dict[str, ParameterValue] = Field(default_factory=dict)

    def matches(self, model: str) -> bool:
        normalized = model.strip().lower()
        family = self.model_family.strip().lower()
        return normalized == family or normalized.startswith(f"{family}-")


class ProviderDescriptor(BaseModel):
    name: str
    label: str
    parameters: list[ParameterDescriptor] = Field(default_factory=list)
    model_constraints: list[ModelParameterConstraint] = Field(default_factory=list)


@runtime_checkable
class ProviderAdapter(Protocol):
    """Structural contract implemented by every model provider integration."""

    name: str

    def descriptor(self) -> ProviderDescriptor: ...

    def normalize_parameters(
        self, model: str, parameters: dict[str, ParameterValue]
    ) -> dict[str, ParameterValue]: ...

    def build(
        self, model: str, parameters: dict[str, ParameterValue]
    ) -> BaseChatModel: ...

    def with_structured_output(
        self, model: BaseChatModel, schema: type[BaseModel]
    ) -> Any: ...
