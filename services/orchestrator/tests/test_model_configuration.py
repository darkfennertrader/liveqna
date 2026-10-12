from app.main import _default_selection
from app.model_factory import ModelFactory
from app.schemas import ModelSelection


def test_default_agent_profiles(monkeypatch):
    for name in (
        "SUPERVISOR_MODEL",
        "SUPERVISOR_TEMPERATURE",
        "SUPERVISOR_REASONING_EFFORT",
        "SUPERVISOR_SERVICE_TIER",
        "QUESTION_AGENT_MODEL",
        "QUESTION_AGENT_TEMPERATURE",
        "QUESTION_AGENT_REASONING_EFFORT",
        "QUESTION_AGENT_SERVICE_TIER",
    ):
        monkeypatch.delenv(name, raising=False)

    supervisor = _default_selection("SUPERVISOR")
    question_agent = _default_selection("QUESTION_AGENT")

    assert supervisor == ModelSelection(
        provider="openai",
        model="gpt-6-luna",
        temperature=None,
        reasoning_effort="none",
        service_tier="fast",
    )
    assert question_agent == ModelSelection(
        provider="openai",
        model="gpt-6.1-sol",
        temperature=None,
        reasoning_effort="low",
        service_tier="fast",
    )


def test_openai_model_uses_responses_api_and_selected_latency_profile(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    selection = ModelSelection(
        provider="openai",
        model="gpt-6.1-sol",
        reasoning_effort="low",
        service_tier="ultrafast",
    )

    model = ModelFactory().get(selection)

    assert model.model_name == "gpt-6.1-sol"
    assert model.reasoning_effort == "low"
    assert model.service_tier == "ultrafast"
    assert model.use_responses_api is True
    assert model.temperature is None
