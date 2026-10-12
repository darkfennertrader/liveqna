from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from .base import (
    ModelConfigurationError,
    ProviderAdapter,
    ProviderDescriptor,
)
from .ollama import OllamaAdapter
from .openai import OpenAIAdapter

if TYPE_CHECKING:
    from ..schemas import ModelSelection


class ProviderRegistry:
    def __init__(self, adapters: Iterable[ProviderAdapter]) -> None:
        self._adapters: dict[str, ProviderAdapter] = {}
        for adapter in adapters:
            if not isinstance(adapter, ProviderAdapter):
                raise TypeError("provider adapter does not satisfy ProviderAdapter")
            name = adapter.name.strip().lower()
            if not name or name in self._adapters:
                raise ValueError(f"invalid or duplicate provider adapter: {name!r}")
            self._adapters[name] = adapter

    @property
    def names(self) -> list[str]:
        return list(self._adapters)

    def get(self, provider: str) -> ProviderAdapter:
        normalized = provider.strip().lower()
        try:
            return self._adapters[normalized]
        except KeyError as exc:
            supported = ", ".join(self.names)
            raise ModelConfigurationError(
                f"unsupported model provider {provider!r}; available: {supported}"
            ) from exc

    def descriptors(self) -> list[ProviderDescriptor]:
        return [adapter.descriptor() for adapter in self._adapters.values()]

    def normalize_selection(self, selection: ModelSelection) -> ModelSelection:
        adapter = self.get(selection.provider)
        parameters = adapter.normalize_parameters(
            selection.model, selection.parameters
        )
        return selection.model_copy(
            update={
                "provider": adapter.name,
                "parameters": parameters,
                "temperature": parameters.get("temperature"),
                "reasoning_effort": parameters.get("reasoning_effort"),
                "service_tier": parameters.get("service_tier"),
            }
        )


def create_provider_registry() -> ProviderRegistry:
    return ProviderRegistry([OpenAIAdapter(), OllamaAdapter()])
