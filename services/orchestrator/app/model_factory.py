from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel

from .providers.base import ModelConfigurationError, ParameterValue, ProviderAdapter
from .providers.registry import ProviderRegistry, create_provider_registry
from .schemas import ModelSelection


class ModelFactory:
    """Validate, build, and reuse models through provider adapters."""

    def __init__(self, registry: ProviderRegistry | None = None) -> None:
        self._registry = registry or create_provider_registry()
        self._models: dict[
            tuple[str, str, tuple[tuple[str, ParameterValue], ...]], BaseChatModel
        ] = {}

    def _resolve(
        self, selection: ModelSelection
    ) -> tuple[ModelSelection, ProviderAdapter]:
        normalized = self._registry.normalize_selection(selection)
        return normalized, self._registry.get(normalized.provider)

    def _get_normalized(
        self, selection: ModelSelection, adapter: ProviderAdapter
    ) -> BaseChatModel:
        parameter_key = tuple(sorted(selection.parameters.items()))
        key = (selection.provider, selection.model, parameter_key)
        if key not in self._models:
            self._models[key] = adapter.build(
                selection.model, selection.parameters
            )
        return self._models[key]

    def get(self, selection: ModelSelection) -> BaseChatModel:
        normalized, adapter = self._resolve(selection)
        return self._get_normalized(normalized, adapter)

    def structured(self, selection: ModelSelection, schema: type[BaseModel]) -> Any:
        normalized, adapter = self._resolve(selection)
        model = self._get_normalized(normalized, adapter)
        return adapter.with_structured_output(model, schema)


__all__ = ["ModelConfigurationError", "ModelFactory"]

