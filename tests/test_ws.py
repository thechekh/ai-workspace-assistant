"""End-to-end WebSocket protocol tests (fake LLM + fakeredis — no network, no cost)."""

import pytest
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient

from assistant.llm.client import FakeLLM
from assistant.main import create_app
from tests.conftest import HermeticSettings, build_seeded_retriever


def collect_until_final(ws) -> list[dict]:
    events: list[dict] = []
    while True:
        event = ws.receive_json()
        events.append(event)
        if event["type"] in {"final", "error"}:
            return events


def test_chat_roundtrip_streams_tokens_and_final(client):
    with client.websocket_connect("/chat") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "session"
        assert hello["session_id"]

        ws.send_json({"type": "user_message", "content": "ping"})
        events = collect_until_final(ws)

        final = events[-1]
        assert final["type"] == "final"
        assert "You said: ping" in final["content"]

        # Streamed tokens must reassemble into exactly the final content
        tokens = [e["content"] for e in events if e["type"] == "token"]
        assert len(tokens) > 1
        assert "".join(tokens) == final["content"]


def test_history_persists_across_reconnects(client):
    # First connection: one exchange. FakeLLM reports prompt size, so the
    # message count proves Redis-backed history reaches the model.
    with client.websocket_connect("/chat") as ws:
        session_id = ws.receive_json()["session_id"]
        ws.send_json({"type": "user_message", "content": "first"})
        events = collect_until_final(ws)
        # system + user -> 2 messages in context
        assert "(2 messages in context)" in events[-1]["content"]

    # Reconnect with the same session id: history must be loaded from Redis.
    with client.websocket_connect(f"/chat?session_id={session_id}") as ws:
        assert ws.receive_json()["session_id"] == session_id
        ws.send_json({"type": "user_message", "content": "second"})
        events = collect_until_final(ws)
        # system + (user, assistant) history + user -> 4 messages in context
        assert "(4 messages in context)" in events[-1]["content"]


def test_invalid_payload_reports_error_and_keeps_socket_alive(client):
    with client.websocket_connect("/chat") as ws:
        ws.receive_json()  # session frame

        ws.send_text("not json at all")
        error = ws.receive_json()
        assert error["type"] == "error"

        # Socket must survive a bad frame
        ws.send_json({"type": "user_message", "content": "still alive?"})
        events = collect_until_final(ws)
        assert events[-1]["type"] == "final"


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dev_console_served_in_debug(client):
    response = client.get("/dev")
    assert response.status_code == 200
    assert "dev console" in response.text


def test_question_triggers_search_docs_tool(client):
    """A docs question runs the full loop: tool_call -> tool_result -> grounded answer."""
    with client.websocket_connect("/chat") as ws:
        ws.receive_json()  # session frame
        ws.send_json({"type": "user_message", "content": "Which service generates PDF invoices?"})
        events = collect_until_final(ws)

        types = [event["type"] for event in events]
        assert "tool_call" in types
        assert "tool_result" in types

        tool_call = next(event for event in events if event["type"] == "tool_call")
        assert tool_call["tool"] == "search_docs"
        assert "invoices" in str(tool_call["arguments"]).lower()

        tool_result = next(event for event in events if event["type"] == "tool_result")
        assert "billing-service" in tool_result["result"]

        final = events[-1]
        assert final["type"] == "final"
        assert "Based on the tool results" in final["content"]


def test_backend_query_param_switches_runtime(client):
    """?backend= picks the runtime per connection, regardless of the default."""
    with client.websocket_connect("/chat?backend=pydantic_ai") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": "ping"})
        events = collect_until_final(ws)
        assert events[-1]["type"] == "final"
        # Both runtimes reply with identical prompt-size accounting
        assert "(2 messages in context)" in events[-1]["content"]

    with client.websocket_connect("/chat?backend=does_not_exist") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": "ping"})
        events = collect_until_final(ws)
        assert events[-1]["type"] == "final"  # silently falls back to the default


@pytest.mark.parametrize("backend", ["custom", "pydantic_ai", "langgraph"])
def test_long_conversations_stay_bounded_by_summary(backend):
    """Once history exceeds the budget, context = system + summary + recent turns
    — the prompt size stops growing, identically on every backend."""
    settings = HermeticSettings(
        llm_provider="fake",
        agent_backend=backend,
        mcp_enabled=False,
        # Tiny budget: every turn's un-summarized tail (~98 chars) exceeds it,
        # so folding happens deterministically on each turn from turn 2 on.
        history_char_budget=60,
        history_keep_recent=2,
    )
    app = create_app(
        settings,
        redis_client=FakeAsyncRedis(decode_responses=True),
        llm=FakeLLM(),
        retriever=build_seeded_retriever(),
    )
    with TestClient(app) as client, client.websocket_connect("/chat") as ws:
        ws.receive_json()
        counts: list[str] = []
        for i in range(5):
            ws.send_json({"type": "user_message", "content": f"m{i}"})
            final = collect_until_final(ws)[-1]
            counts.append(final["content"])
        # After folding kicks in: system prompt + summary + 2 recent + user = 5, stable
        assert "(5 messages in context)" in counts[-1]
        assert "(5 messages in context)" in counts[-2]


def test_fakeredis_url_runs_without_infrastructure():
    """ASSISTANT_REDIS_URL=fakeredis:// must give a fully working chat with zero infra."""
    settings = HermeticSettings(redis_url="fakeredis://", mcp_enabled=False)
    app = create_app(settings, llm=FakeLLM(), retriever=build_seeded_retriever())
    with TestClient(app) as client, client.websocket_connect("/chat") as ws:
        ws.receive_json()  # session frame
        ws.send_json({"type": "user_message", "content": "no docker needed"})
        events = collect_until_final(ws)
        assert events[-1]["type"] == "final"
        assert "no docker needed" in events[-1]["content"]
