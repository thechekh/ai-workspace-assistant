"""Phase 9 observability: TurnSummary frame, /metrics, deep health, audit trail.

Everything runs on FakeLLM + fakeredis/in-memory Qdrant — deterministic, no
network. Prometheus counters are process-global, so tests assert presence and
structure, never exact values.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from assistant.llm.client import FakeLLM, TextDelta, ToolCallRequest, UsageEvent
from assistant.main import create_app
from assistant.telemetry import InstrumentedLLM, TurnStats, current_turn_stats
from tests.conftest import (
    HermeticSettings,
    build_seeded_retriever,
    collect_until_final,
    make_client,
)


def run_one_turn(ws, content: str) -> tuple[list[dict], dict]:
    """Send one message; return (events up to final, the trailing turn frame)."""
    ws.send_json({"type": "user_message", "content": content})
    events = collect_until_final(ws)
    summary = ws.receive_json()
    return events, summary


# --- TurnSummary WS frame (parametrized over all three backends) -----------


def test_turn_summary_frame_follows_final(client, settings):
    with client.websocket_connect("/chat") as ws:
        ws.receive_json()  # session frame
        events, summary = run_one_turn(ws, "ping")

        assert events[-1]["type"] == "final"
        assert summary["type"] == "turn"
        assert summary["backend"] == settings.agent_backend
        assert len(summary["turn_id"]) == 12
        assert summary["duration_ms"] >= 0
        assert summary["first_token_ms"] is not None
        assert summary["llm_steps"] >= 1
        assert summary["tool_calls"] == []
        assert summary["completion_tokens"] > 0
        assert summary["usage_estimated"] is True  # FakeLLM reports no usage


def test_turn_summary_lists_tool_calls(client):
    with client.websocket_connect("/chat") as ws:
        ws.receive_json()
        events, summary = run_one_turn(ws, "Which service generates PDF invoices?")

        assert any(event["type"] == "tool_call" for event in events)
        assert summary["tool_calls"] == ["search_docs"]
        assert summary["llm_steps"] >= 1


# --- /metrics ---------------------------------------------------------------


def test_metrics_endpoint_exposes_assistant_series(client):
    with client.websocket_connect("/chat") as ws:
        ws.receive_json()
        run_one_turn(ws, "ping")

    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.text
    for series in (
        "assistant_turns_total",
        "assistant_turn_seconds",
        "assistant_llm_step_seconds",
        "assistant_tokens_total",
    ):
        assert series in body


# --- /api/health (deep) ------------------------------------------------------


def test_deep_health_reports_every_component(client):
    payload = client.get("/api/health").json()
    assert payload["status"] == "ok"
    components = payload["components"]
    assert components["redis"]["status"] == "ok"
    assert "latency_ms" in components["redis"]
    # Tests inject a retriever, so there is no live Qdrant client to ping.
    assert components["qdrant"]["status"] == "skipped"
    assert components["llm"]["status"] == "ok"
    assert components["llm"]["provider"] == "fake"
    assert components["mcp"]["status"] == "disabled"  # mcp_enabled=False fixture


def test_deep_health_degrades_when_redis_is_down():
    class BrokenRedis:
        async def ping(self) -> None:
            raise ConnectionError("redis unreachable")

    settings = HermeticSettings(llm_provider="fake", mcp_enabled=False, redis_url="fakeredis://")
    app = create_app(settings, llm=FakeLLM(), retriever=build_seeded_retriever())
    with TestClient(app) as client:
        app.state.redis = BrokenRedis()
        payload = client.get("/api/health").json()
    assert payload["status"] == "degraded"
    assert payload["components"]["redis"]["status"] == "error"
    assert "unreachable" in payload["components"]["redis"]["detail"]


# --- /api/sessions/{id}/turns audit trail ------------------------------------


def test_turns_audit_trail_records_timeline(client):
    with client.websocket_connect("/chat") as ws:
        session_id = ws.receive_json()["session_id"]
        run_one_turn(ws, "Which service generates PDF invoices?")
        run_one_turn(ws, "ping")

        # The audit write lands right after the turn frame is sent; poll briefly.
        payload: dict = {"count": 0}
        for _ in range(50):
            payload = client.get(f"/api/sessions/{session_id}/turns").json()
            if payload["count"] == 2:
                break

    assert payload["session_id"] == session_id
    assert payload["count"] == 2
    first, second = payload["turns"]
    assert first["tool_calls"] == ["search_docs"]
    event_types = [event["type"] for event in first["events"]]
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert event_types[-1] == "final"
    assert second["tool_calls"] == []
    assert all(event["ms"] >= 0 for turn in (first, second) for event in turn["events"])


def test_single_turn_endpoint_returns_just_that_turn(client):
    """The UI's details panel fetches one turn, not the whole audit list."""
    with client.websocket_connect("/chat") as ws:
        session_id = ws.receive_json()["session_id"]
        _, summary = run_one_turn(ws, "Which service generates PDF invoices?")
        run_one_turn(ws, "ping")

        turn_id = summary["turn_id"]
        response = client.get(f"/api/sessions/{session_id}/turns/{turn_id}")
        for _ in range(50):  # the audit write lands just after the turn frame
            if response.status_code == 200:
                break
            response = client.get(f"/api/sessions/{session_id}/turns/{turn_id}")

    payload = response.json()
    assert payload["turn_id"] == turn_id
    assert payload["tool_calls"] == ["search_docs"]
    assert [event["type"] for event in payload["events"]][-1] == "final"
    # It is one record, not the {session_id, count, turns} envelope.
    assert "turns" not in payload


def test_single_turn_endpoint_404s_for_unknown_turn(client):
    with client.websocket_connect("/chat") as ws:
        session_id = ws.receive_json()["session_id"]
        run_one_turn(ws, "ping")
    assert client.get(f"/api/sessions/{session_id}/turns/deadbeef1234").status_code == 404


def test_turns_endpoint_requires_token_when_auth_enabled():
    with make_client(auth_token=SecretStr("s3cret")) as client:
        assert client.get("/api/sessions/abc/turns").status_code == 401
        ok = client.get("/api/sessions/abc/turns", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200
        assert ok.json() == {"session_id": "abc", "count": 0, "turns": []}


# --- InstrumentedLLM unit behaviour ------------------------------------------


class FlatScriptedLLM:
    """Yields one fixed event sequence (unlike conftest's step-based ScriptedLLM)."""

    def __init__(self, events) -> None:
        self._events = events

    async def stream_step(self, messages, tools=None):
        for event in self._events:
            yield event


async def test_instrumented_llm_consumes_usage_and_accumulates_stats():
    inner = FlatScriptedLLM(
        [
            TextDelta(text="hello "),
            TextDelta(text="world"),
            ToolCallRequest(id="c1", name="search_docs", arguments="{}"),
            UsageEvent(prompt_tokens=120, completion_tokens=7),
        ]
    )
    llm = InstrumentedLLM(inner, provider="fake", model="test-model")

    stats = TurnStats()
    token = current_turn_stats.set(stats)
    try:
        from assistant.agent.base import ChatMessage

        events = [
            event async for event in llm.stream_step([ChatMessage(role="user", content="hi")])
        ]
    finally:
        current_turn_stats.reset(token)

    # UsageEvent is telemetry-only: consumed by the wrapper, never forwarded.
    assert [type(event).__name__ for event in events] == [
        "TextDelta",
        "TextDelta",
        "ToolCallRequest",
    ]
    assert stats.llm_steps == 1
    assert stats.prompt_tokens == 120  # real usage, not the chars//4 estimate
    assert stats.completion_tokens == 7
    assert stats.usage_estimated is False
    assert stats.llm_ms >= 0


def test_logfire_instrumentation_excludes_scrapes_and_health_polls(monkeypatch) -> None:
    """Prometheus hits /metrics every 5 s and the UI polls /api/health every
    10 s. Instrumented, those alone were 1,000+ traces an hour in every cloud
    dashboard, burying the real turns. The exclusion is what keeps a Logfire
    or Langfuse view readable, so it is pinned here — with a stub Logfire, no
    token, no network."""
    import sys
    import types

    from fastapi import FastAPI

    from assistant.observability import NOISY_PATHS, configure_observability
    from tests.conftest import HermeticSettings

    calls: dict[str, dict] = {}
    stub = types.SimpleNamespace()  # anything in sys.modules satisfies `import logfire`
    stub.configure = lambda **kwargs: calls.setdefault("configure", kwargs)
    stub.instrument_fastapi = lambda app, **kwargs: calls.setdefault("fastapi", kwargs)
    stub.instrument_httpx = lambda **kwargs: calls.setdefault("httpx", kwargs)
    stub.instrument_pydantic_ai = lambda **kwargs: calls.setdefault("pydantic_ai", kwargs)
    stub.SamplingOptions = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "logfire", stub)

    settings = HermeticSettings(
        llm_provider="fake", mcp_enabled=False, logfire_token=SecretStr("stub-token")
    )
    configure_observability(FastAPI(), settings)

    assert calls["configure"]["send_to_logfire"] == "if-token-present"
    excluded = calls["fastapi"]["excluded_urls"]
    assert excluded == NOISY_PATHS
    for path in ("/metrics", "/api/health"):
        assert path in excluded
    # The straggler: httpx has no URL exclusion, so the head sampler catches
    # what excluded_urls cannot (the health check's own Qdrant call).
    assert calls["configure"]["sampling"]["head"].get_description() == "DropNoisyRootSpans"


@pytest.mark.parametrize(
    ("name", "kept"),
    [
        ("agent.turn", True),
        ("HTTP /chat ? backend='custom'", True),
        ("POST api.openai.com/v1/chat/completions", True),  # a real root call, e.g. from evals
        ("GET /metrics", False),
        ("GET /api/health", False),
        ("POST localhost/collections/docs/points/count", False),  # the health check's Qdrant call
    ],
)
def test_noise_sampler_drops_only_machinery_root_spans(name: str, kept: bool) -> None:
    from opentelemetry.sdk.trace.sampling import Decision

    from assistant.observability import make_noise_sampler

    result = make_noise_sampler().should_sample(None, 1, name)
    assert (result.decision == Decision.RECORD_AND_SAMPLE) is kept


def test_noise_sampler_follows_the_parent_for_child_spans() -> None:
    """A child of a sampled turn is kept whatever its name; a child of a
    dropped root is dropped too, so no orphan fragments reach a dashboard."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace.sampling import Decision
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

    from assistant.observability import make_noise_sampler

    sampler = make_noise_sampler()
    for sampled, expected in ((True, Decision.RECORD_AND_SAMPLE), (False, Decision.DROP)):
        flags = TraceFlags(TraceFlags.SAMPLED if sampled else TraceFlags.DEFAULT)
        parent = NonRecordingSpan(
            SpanContext(trace_id=7, span_id=3, is_remote=False, trace_flags=flags)
        )
        context = trace.set_span_in_context(parent)
        assert sampler.should_sample(context, 7, "GET /metrics").decision == expected


@pytest.mark.parametrize(
    ("attributes", "kept"),
    [
        # httpx spans are created as a bare method name and renamed only after
        # sampling, so the decision has to come from the attributes.
        ({"http.url": "http://localhost:6333/collections/docs/points/count"}, False),
        ({"url.full": "http://127.0.0.1:6333/collections/docs/exists"}, False),
        ({"server.address": "localhost", "server.port": 6333}, False),
        ({"http.url": "https://api.openai.com/v1/chat/completions"}, True),
        ({"server.address": "api.githubcopilot.com"}, True),
        (None, True),
    ],
)
def test_noise_sampler_drops_root_http_calls_to_local_infrastructure(
    attributes: dict | None, kept: bool
) -> None:
    from opentelemetry.sdk.trace.sampling import Decision
    from opentelemetry.trace import SpanKind

    from assistant.observability import make_noise_sampler

    result = make_noise_sampler().should_sample(
        None, 1, "POST", kind=SpanKind.CLIENT, attributes=attributes
    )
    assert (result.decision == Decision.RECORD_AND_SAMPLE) is kept


def test_noise_sampler_never_judges_server_spans_by_host() -> None:
    """The WebSocket server span for /chat carries server.address=127.0.0.1
    too; judging it by host dropped every real turn of a locally served app."""
    from opentelemetry.sdk.trace.sampling import Decision
    from opentelemetry.trace import SpanKind

    from assistant.observability import make_noise_sampler

    attributes = {
        "server.address": "127.0.0.1",
        "http.url": "ws://127.0.0.1:8000/chat?backend=custom",
    }
    result = make_noise_sampler().should_sample(
        None, 1, "HTTP /chat", kind=SpanKind.SERVER, attributes=attributes
    )
    assert result.decision == Decision.RECORD_AND_SAMPLE


def test_otlp_and_langfuse_exporters_are_wired_with_their_endpoints(monkeypatch) -> None:
    """Without Logfire, a plain SDK provider carries one exporter per
    destination: Jaeger's OTLP endpoint, and Langfuse's with basic auth.
    The exporters and the provider are stubbed — nothing opens a socket."""
    import base64

    from fastapi import FastAPI
    from pydantic import SecretStr

    from assistant.observability import configure_observability
    from tests.conftest import HermeticSettings

    exporters: list[dict[str, object]] = []
    installed: list[object] = []

    class StubExporter:
        def __init__(self, endpoint: str, headers: dict[str, str] | None = None) -> None:
            exporters.append({"endpoint": endpoint, "headers": headers or {}})

    class StubProcessor:
        def __init__(self, exporter: object) -> None:
            self.exporter = exporter

        def on_start(self, span, parent_context=None) -> None:
            return None

        def on_end(self, span) -> None:
            return None

        def shutdown(self) -> None:
            return None

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return True

    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter", StubExporter
    )
    monkeypatch.setattr("opentelemetry.sdk.trace.export.BatchSpanProcessor", StubProcessor)
    monkeypatch.setattr("opentelemetry.trace.set_tracer_provider", installed.append)

    settings = HermeticSettings(
        otlp_endpoint="http://localhost:4318/",
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key=SecretStr("sk-lf-test"),
        langfuse_host="https://cloud.langfuse.com/",
    )
    configure_observability(FastAPI(), settings)

    assert [e["endpoint"] for e in exporters] == [
        "http://localhost:4318/v1/traces",
        "https://cloud.langfuse.com/api/public/otel/v1/traces",
    ]
    expected = base64.b64encode(b"pk-lf-test:sk-lf-test").decode()
    assert exporters[1]["headers"] == {"Authorization": f"Basic {expected}"}
    from opentelemetry.sdk.trace import TracerProvider

    assert len(installed) == 1, "one tracer provider, installed once"
    provider = installed[0]
    assert isinstance(provider, TracerProvider)
    assert provider.sampler.get_description() == "DropNoisyRootSpans"
