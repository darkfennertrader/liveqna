import pytest

from app.providers.base import ModelConfigurationError, ProviderAdapter
from app.providers.openai import OpenAIAdapter
from app.providers.registry import create_provider_registry
from app.schemas import ModelSelection


def test_adapters_satisfy_provider_protocol() -> None:
    assert isinstance(OpenAIAdapter(), ProviderAdapter)


def test_registry_publishes_dynamic_parameter_catalog() -> None:
    registry = create_provider_registry()

    assert registry.names == ["openai", "ollama"]
    catalog = {descriptor.name: descriptor for descriptor in registry.descriptors()}
    assert {parameter.name for parameter in catalog["openai"].parameters} == {
        "reasoning_effort",
        "service_tier",
        "temperature",
    }
    assert {parameter.name for parameter in catalog["ollama"].parameters} == {
        "temperature"
    }


def test_unknown_provider_fails_before_inference() -> None:
    selection = ModelSelection(provider="other", model="model")

    with pytest.raises(ModelConfigurationError, match="unsupported model provider"):
        create_provider_registry().normalize_selection(selection)


def test_provider_rejects_parameters_it_does_not_own() -> None:
    selection = ModelSelection(
        provider="ollama",
        model="llama3.2",
        parameters={"service_tier": "fast"},
    )

    with pytest.raises(ModelConfigurationError, match="service_tier"):
        create_provider_registry().normalize_selection(selection)
