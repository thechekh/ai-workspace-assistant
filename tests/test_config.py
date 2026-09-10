import pytest
from pydantic import SecretStr, TypeAdapter

from assistant.api.schemas import ServerEvent
from assistant.llm.client import FakeLLM, OpenAICompatibleLLM, build_llm
from tests.conftest import HermeticSettings


def test_defaults_are_offline_and_free():
    settings = HermeticSettings()
    assert settings.llm_provider == "fake"
    assert settings.agent_backend == "custom"


def test_env_prefix_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ASSISTANT_LLM_PROVIDER", "openai")
    monkeypatch.setenv("ASSISTANT_LLM_API_KEY", "sk-test")
    settings = HermeticSettings()
    assert settings.llm_provider == "openai"


def test_build_llm_fake_by_default():
    assert isinstance(build_llm(HermeticSettings()), FakeLLM)


def test_build_llm_requires_key_for_hosted_providers():
    settings = HermeticSettings(llm_provider="openai")
    with pytest.raises(ValueError, match="ASSISTANT_LLM_API_KEY"):
        build_llm(settings)


def test_build_llm_openai_uses_openai_compatible_client():
    settings = HermeticSettings(llm_provider="openai", llm_api_key=SecretStr("sk-test"))
    llm = build_llm(settings)
    assert isinstance(llm, OpenAICompatibleLLM)


def test_server_event_discriminated_union_parses():
    adapter: TypeAdapter[object] = TypeAdapter(ServerEvent)
    token = adapter.validate_python({"type": "token", "content": "hi"})
    session = adapter.validate_python({"type": "session", "session_id": "abc"})
    assert type(token).__name__ == "TokenEvent"
    assert type(session).__name__ == "SessionStarted"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("history_keep_recent", 0),  # would fold an empty slice every turn
        ("history_char_budget", 0),
        ("session_ttl_seconds", 0),
        ("rate_limit_turns_per_minute", -1),  # 0 is "disabled"; negative is a typo
    ],
)
def test_integer_settings_are_bounded(field: str, value: int):
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match=field):
        HermeticSettings.model_validate({field: value})


def test_zero_disables_a_rate_limit_bucket_and_is_still_valid():
    assert HermeticSettings(rate_limit_turns_per_minute=0).rate_limit_turns_per_minute == 0


def test_a_blank_optional_setting_means_unset():
    """`ASSISTANT_OTLP_ENDPOINT=` is how a .env says "off".

    It used to mean "an empty endpoint": not None, so `configure_observability`
    switched tracing on and pointed the exporter at nothing, logging a
    connection warning and an export error every few seconds forever.
    """
    blank = HermeticSettings.model_validate(
        {
            "otlp_endpoint": "",
            "logfire_token": "   ",
            "langfuse_public_key": "",
            "langfuse_secret_key": "",
            "llm_api_key": "",
            "embedding_api_key": "",
            "voyage_api_key": "",
            "github_token": "",
            "auth_token": "",
            "llm_base_url": "",
        }
    )
    assert blank.otlp_endpoint is None
    assert blank.logfire_token is None
    assert blank.langfuse_public_key is None
    assert blank.langfuse_secret_key is None
    assert blank.llm_api_key is None
    assert blank.embedding_api_key is None
    assert blank.voyage_api_key is None
    assert blank.github_token is None
    assert blank.auth_token is None
    assert blank.llm_base_url is None

    # A real value still arrives intact.
    set_up = HermeticSettings(otlp_endpoint="http://localhost:4318")
    assert set_up.otlp_endpoint == "http://localhost:4318"


def test_blank_credentials_leave_tracing_inert(monkeypatch: pytest.MonkeyPatch):
    """The observable half of the fix: no exporter, no provider, no retries."""
    from fastapi import FastAPI

    from assistant.observability import configure_observability

    installed: list[object] = []
    exporters: list[str] = []
    monkeypatch.setattr("opentelemetry.trace.set_tracer_provider", installed.append)
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
        lambda **kwargs: exporters.append(str(kwargs)),
    )

    settings = HermeticSettings.model_validate(
        {"otlp_endpoint": "", "logfire_token": "", "langfuse_public_key": ""}
    )
    configure_observability(FastAPI(), settings)
    assert installed == [], "blank settings must not install a tracer provider"
    assert exporters == [], "blank settings must not build an exporter"
