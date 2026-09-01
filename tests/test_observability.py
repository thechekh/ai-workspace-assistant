"""Phase 9 observability: TurnSummary frame, /metrics, deep health, audit trail.

Everything runs on FakeLLM + fakeredis/in-memory Qdrant — deterministic, no
network. Prometheus counters are process-global, so tests assert presence and
structure, never exact values.
"""

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
