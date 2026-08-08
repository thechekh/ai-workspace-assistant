"""LLM failure UX: 429 backoff-retry, Groq tool_use_failed retry + salvage,
friendly WS error frames, and indicative cost accounting."""

import json
from types import SimpleNamespace

import httpx
import pytest
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient
from openai import APIConnectionError, APIError, BadRequestError, RateLimitError

from assistant.agent.base import ChatMessage
from assistant.llm.client import (
    FakeLLM,
    OpenAICompatibleLLM,
    TextDelta,
    ToolCallRequest,
    parse_leaked_tool_calls,
)
from assistant.llm.errors import describe_llm_error
from assistant.main import create_app
from assistant.telemetry import estimate_cost_usd
from tests.conftest import HermeticSettings, collect_until_final


def _http_error(cls, status: int, headers: dict[str, str] | None = None):
    request = httpx.Request("POST", "https://api.test/v1/chat/completions")
    response = httpx.Response(status, request=request, headers=headers or {})
    return cls("boom", response=response, body=None)


# --- _describe_llm_error mapping ---------------------------------------------


class _StatusError(Exception):
    """Duck-typed provider error (openai APIStatusError / pydantic-ai ModelHTTPError shape)."""

    def __init__(self, status_code: int, message: str = "provider says no") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@pytest.mark.parametrize(
    ("status", "kind", "needle"),
    [
        (429, "rate_limited", "rate limit"),
        (401, "auth_failed", "ASSISTANT_LLM_API_KEY"),
        (403, "auth_failed", "ASSISTANT_LLM_API_KEY"),
        (404, "model_unavailable", "ASSISTANT_LLM_MODEL"),
        (500, "provider_error", "provider error"),
        (400, "llm_bad_request", "rejected"),
    ],
)
def test_describe_llm_error_maps_status_codes(status, kind, needle):
    mapped = describe_llm_error(_StatusError(status))
    assert mapped is not None
    assert mapped[0] == kind
    assert needle.lower() in mapped[1].lower()


def test_describe_llm_error_walks_the_cause_chain():
    try:
        try:
            raise _StatusError(429)
        except _StatusError as inner:
            raise RuntimeError("backend wrapper") from inner
    except RuntimeError as wrapped:
        mapped = describe_llm_error(wrapped)
    assert mapped is not None
    assert mapped[0] == "rate_limited"


def test_describe_llm_error_handles_connection_errors():
    exc = APIConnectionError(request=httpx.Request("POST", "https://api.test"))
    mapped = describe_llm_error(exc)
    assert mapped is not None
    assert mapped[0] == "provider_unreachable"


def test_describe_llm_error_passes_on_unrelated_exceptions():
    assert describe_llm_error(ValueError("nothing to do with LLMs")) is None


# --- WS error frames ----------------------------------------------------------


class FailingAgent:
    """AgentBackend that dies with a given exception on every run."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def run(self, history, user_message):
        raise self._exc
        yield  # pragma: no cover — makes this an async generator


def _client_with_agent(agent) -> TestClient:
    settings = HermeticSettings(llm_provider="fake", mcp_enabled=False)
    app = create_app(
        settings, redis_client=FakeAsyncRedis(decode_responses=True), llm=FakeLLM(), agent=agent
    )
    return TestClient(app)


def test_rate_limit_becomes_a_friendly_error_frame():
    with (
        _client_with_agent(FailingAgent(_StatusError(429))) as client,
        client.websocket_connect("/chat") as ws,
    ):
        ws.receive_json()  # session frame
        ws.send_json({"type": "user_message", "content": "hi"})
        events = collect_until_final(ws)
    assert events[-1]["type"] == "error"
    assert "rate limit" in events[-1]["message"].lower()


def test_unknown_crash_keeps_the_generic_error_frame():
    with (
        _client_with_agent(FailingAgent(ValueError("boom"))) as client,
        client.websocket_connect("/chat") as ws,
    ):
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": "hi"})
        events = collect_until_final(ws)
    assert events[-1]["type"] == "error"
    assert "server error" in events[-1]["message"]


# --- 429 retry inside OpenAICompatibleLLM -------------------------------------


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record backoff delays instead of really sleeping (keeps the suite fast)."""
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("assistant.llm.client.asyncio.sleep", fake_sleep)
    return delays


async def test_create_stream_retries_rate_limits_with_retry_after(slept: list[float]):
    llm = OpenAICompatibleLLM(model="m", api_key="k", base_url="https://api.test/v1")
    attempts = 0

    async def create(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise _http_error(RateLimitError, 429, headers={"retry-after": "0"})
        return "the-stream"

    llm._client.chat.completions.create = create  # type: ignore[method-assign]
    stream = await llm._create_stream({"model": "m", "messages": []})
    assert stream == "the-stream"
    assert attempts == 3
    # `retry-after: 0` means "retry now" — it must be honored, not treated as
    # absent and replaced by the 2s/4s default backoff.
    assert slept == [0.0, 0.0]


async def test_create_stream_uses_default_backoff_without_retry_after(slept: list[float]):
    llm = OpenAICompatibleLLM(model="m", api_key="k", base_url="https://api.test/v1")
    attempts = 0

    async def create(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise _http_error(RateLimitError, 429)  # no retry-after header
        return "the-stream"

    llm._client.chat.completions.create = create  # type: ignore[method-assign]
    assert await llm._create_stream({"model": "m", "messages": []}) == "the-stream"
    assert slept == [2.0, 4.0]


async def test_create_stream_caps_absurd_retry_after(slept: list[float]):
    llm = OpenAICompatibleLLM(model="m", api_key="k", base_url="https://api.test/v1")

    async def create(**kwargs):
        raise _http_error(RateLimitError, 429, headers={"retry-after": "3600"})

    llm._client.chat.completions.create = create  # type: ignore[method-assign]
    with pytest.raises(RateLimitError):
        await llm._create_stream({"model": "m", "messages": []})
    assert slept == [15.0, 15.0]  # _MAX_RETRY_DELAY_S, not an hour


async def test_create_stream_gives_up_after_retry_budget(slept: list[float]):
    llm = OpenAICompatibleLLM(model="m", api_key="k", base_url="https://api.test/v1")

    async def create(**kwargs):
        raise _http_error(RateLimitError, 429, headers={"retry-after": "0"})

    llm._client.chat.completions.create = create  # type: ignore[method-assign]
    with pytest.raises(RateLimitError):
        await llm._create_stream({"model": "m", "messages": []})
    assert len(slept) == 2  # two retries, then give up


async def test_create_stream_drops_stream_options_on_bad_request():
    llm = OpenAICompatibleLLM(model="m", api_key="k", base_url="https://api.test/v1")
    seen_kwargs: list[dict] = []

    async def create(**kwargs):
        seen_kwargs.append(kwargs)
        if "stream_options" in kwargs:
            raise _http_error(BadRequestError, 400)
        return "the-stream"

    llm._client.chat.completions.create = create  # type: ignore[method-assign]
    kwargs = {"model": "m", "messages": [], "stream_options": {"include_usage": True}}
    stream = await llm._create_stream(kwargs)
    assert stream == "the-stream"
    assert "stream_options" in seen_kwargs[0]
    assert "stream_options" not in seen_kwargs[1]


# --- Groq "tool_use_failed" mid-stream flake -----------------------------------


def _tool_use_error(failed_generation: str | None = None) -> APIError:
    body: dict[str, object] = {"code": "tool_use_failed"}
    if failed_generation is not None:
        body["failed_generation"] = failed_generation
    return APIError(
        "Failed to call a function. Please adjust your prompt.",
        httpx.Request("POST", "https://api.test/v1/chat/completions"),
        body=body,
    )


def _chunk(content: str) -> SimpleNamespace:
    delta = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=delta)])


class _FailingStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise _tool_use_error()


class _GoodStream:
    def __init__(self, *texts: str) -> None:
        self._chunks = iter([_chunk(text) for text in texts])

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration from None


async def test_stream_step_retries_groq_tool_use_failure():
    llm = OpenAICompatibleLLM(model="m", api_key="k", base_url="https://api.test/v1")
    streams = iter([_FailingStream(), _GoodStream("hel", "lo")])

    async def fake_create_stream(create_kwargs):
        return next(streams)

    llm._create_stream = fake_create_stream  # type: ignore[method-assign]
    events = [event async for event in llm.stream_step([ChatMessage(role="user", content="hi")])]
    assert [event.text for event in events if isinstance(event, TextDelta)] == ["hel", "lo"]


async def test_stream_step_never_retries_after_text_was_forwarded():
    class FailsMidStream:
        def __init__(self) -> None:
            self._sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._sent:
                raise _tool_use_error()
            self._sent = True
            return _chunk("partial ")

    llm = OpenAICompatibleLLM(model="m", api_key="k", base_url="https://api.test/v1")

    async def fake_create_stream(create_kwargs):
        return FailsMidStream()

    llm._create_stream = fake_create_stream  # type: ignore[method-assign]
    received: list[str] = []
    # PT012: the loop must run *inside* the block — we assert both that the
    # error escapes and exactly what was emitted before it did.
    with pytest.raises(APIError):  # noqa: PT012
        async for event in llm.stream_step([ChatMessage(role="user", content="hi")]):
            if isinstance(event, TextDelta):
                received.append(event.text)
    assert received == ["partial "]  # no duplicated output from a blind retry


def test_describe_llm_error_maps_tool_use_failure():
    mapped = describe_llm_error(_tool_use_error())
    assert mapped is not None
    assert mapped[0] == "tool_use_failed"
    assert "send the message again" in mapped[1]


# --- leaked tool-call salvage (llama emits tool syntax as text) ----------------


def test_parse_leaked_tool_calls_dot_and_equals_forms():
    dot = '<function.code__search_code>{"pattern":"class CustomAgent","max_results":10}</function>'
    calls = parse_leaked_tool_calls(dot)
    assert calls is not None
    assert calls[0].name == "code__search_code"
    assert json.loads(calls[0].arguments) == {"pattern": "class CustomAgent", "max_results": 10}

    nested = '<function=search_docs>{"query":"x","filters":{"lang":"en"}}'
    calls = parse_leaked_tool_calls(nested)
    assert calls is not None
    assert json.loads(calls[0].arguments)["filters"] == {"lang": "en"}

    parens = '<function(github__list_pull_requests){"limit":5,"state":"open"}</function>'
    calls = parse_leaked_tool_calls(parens)
    assert calls is not None
    assert calls[0].name == "github__list_pull_requests"
    assert json.loads(calls[0].arguments) == {"limit": 5, "state": "open"}


def test_parse_leaked_tool_calls_bare_json_form():
    calls = parse_leaked_tool_calls('{"name": "search_docs", "arguments": {"query": "x"}}')
    assert calls is not None
    assert calls[0].name == "search_docs"
    assert json.loads(calls[0].arguments) == {"query": "x"}


def test_parse_leaked_tool_calls_rejects_prose():
    assert parse_leaked_tool_calls("The <function> tag is used in HTML docs.") is None
    assert parse_leaked_tool_calls("a perfectly ordinary answer") is None


async def test_stream_step_converts_leaked_text_tool_call():
    llm = OpenAICompatibleLLM(model="m", api_key="k", base_url="https://api.test/v1")

    async def fake_create_stream(create_kwargs):
        return _GoodStream(
            "<function.gith", "ub__list_pull_requests>", '{"state":"open"}', "</function>"
        )

    llm._create_stream = fake_create_stream  # type: ignore[method-assign]
    events = [event async for event in llm.stream_step([ChatMessage(role="user", content="hi")])]
    assert not any(isinstance(event, TextDelta) for event in events)
    calls = [event for event in events if isinstance(event, ToolCallRequest)]
    assert len(calls) == 1
    assert calls[0].name == "github__list_pull_requests"
    assert json.loads(calls[0].arguments) == {"state": "open"}


async def test_stream_step_still_streams_text_starting_with_angle_bracket():
    llm = OpenAICompatibleLLM(model="m", api_key="k", base_url="https://api.test/v1")

    async def fake_create_stream(create_kwargs):
        return _GoodStream("<p>", "hello")

    llm._create_stream = fake_create_stream  # type: ignore[method-assign]
    events = [event async for event in llm.stream_step([ChatMessage(role="user", content="hi")])]
    assert [event.text for event in events if isinstance(event, TextDelta)] == ["<p>", "hello"]


async def test_stream_step_recovers_call_from_failed_generation():
    llm = OpenAICompatibleLLM(model="m", api_key="k", base_url="https://api.test/v1")
    leaked = '<function=search_docs>{"query":"openaddons"}</function>'
    attempts = 0

    class AlwaysFailing:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise _tool_use_error(failed_generation=leaked)

    async def fake_create_stream(create_kwargs):
        nonlocal attempts
        attempts += 1
        return AlwaysFailing()

    llm._create_stream = fake_create_stream  # type: ignore[method-assign]
    events = [event async for event in llm.stream_step([ChatMessage(role="user", content="hi")])]
    assert attempts == 3  # initial try + 2 retries, then salvage instead of raising
    calls = [event for event in events if isinstance(event, ToolCallRequest)]
    assert len(calls) == 1
    assert calls[0].name == "search_docs"
    assert json.loads(calls[0].arguments) == {"query": "openaddons"}


# --- cost accounting -----------------------------------------------------------


def test_estimate_cost_usd_uses_the_price_table():
    # llama-3.3-70b-versatile: $0.59 / $0.79 per 1M tokens
    assert estimate_cost_usd("llama-3.3-70b-versatile", 1_000_000, 0) == pytest.approx(0.59)
    assert estimate_cost_usd("llama-3.3-70b-versatile", 0, 1_000_000) == pytest.approx(0.79)
    assert estimate_cost_usd("llama-3.3-70b-versatile", 1000, 200) == pytest.approx(0.000748)


def test_estimate_cost_usd_prices_unknown_models_at_zero():
    assert estimate_cost_usd("", 5000, 5000) == 0.0
    assert estimate_cost_usd("some-unknown-model", 5000, 5000) == 0.0


def test_fake_provider_turns_cost_nothing(client):
    with client.websocket_connect("/chat") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": "ping"})
        collect_until_final(ws)
        summary = ws.receive_json()
    assert summary["type"] == "turn"
    assert summary["cost_usd"] == 0.0
