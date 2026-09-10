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
