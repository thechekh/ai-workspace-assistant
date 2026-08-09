"""End-to-end WebSocket protocol tests (fake LLM + fakeredis — no network, no cost)."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from fakeredis import FakeAsyncRedis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistant.agent.base import ChatMessage
from assistant.config import AgentBackendName
from assistant.llm.client import FakeLLM, LLMEvent, TextDelta, ToolSpec
from assistant.main import create_app
from tests.conftest import HermeticSettings, build_seeded_retriever, collect_until_final


class SlowLLM:
    """Streams one word at a time with a real await between each.

    Cancellation is only observable against a provider that is still running,
    and every await is a point where the cancellation can actually land — a
    synchronous fake would finish the turn before the Stop frame is read.
    """

    # Short enough that an *un*-cancelled turn still finishes fast, long
    # enough that the Stop frame lands mid-stream rather than after the end.
    def __init__(self, words: int = 40, delay: float = 0.02) -> None:
        self._words = words
        self._delay = delay
        self.cleaned_up = False

    async def stream_step(
        self, messages: list[ChatMessage], tools: list[ToolSpec] | None = None
    ) -> AsyncIterator[LLMEvent]:
        try:
            for i in range(self._words):
                await asyncio.sleep(self._delay)
                yield TextDelta(text=f"word{i} ")
        finally:
            # Proves the generator is closed rather than left dangling — a
            # leaked one would hold the provider's HTTP stream open.
            self.cleaned_up = True


def _slow_app(llm: SlowLLM, backend: AgentBackendName = "custom") -> FastAPI:
    settings = HermeticSettings(llm_provider="fake", agent_backend=backend, mcp_enabled=False)
    return create_app(
        settings,
        redis_client=FakeAsyncRedis(decode_responses=True),
        llm=llm,
        retriever=build_seeded_retriever(),
    )


def _receive_until(ws, wanted: str, limit: int = 200) -> list[dict]:
    """Read frames until one of type `wanted` arrives (inclusive)."""
    seen: list[dict] = []
    for _ in range(limit):
        frame = ws.receive_json()
        seen.append(frame)
        if frame["type"] == wanted:
            return seen
    raise AssertionError(f"no {wanted!r} frame in {len(seen)} frames: {[f['type'] for f in seen]}")


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


def test_cancel_stops_the_turn_and_reports_it(caplog):
    """Stop mid-stream: the turn ends with a cancelled summary, not an error."""
    llm = SlowLLM()
    app = _slow_app(llm)
    with TestClient(app) as client, client.websocket_connect("/chat") as ws:
        ws.receive_json()  # session frame
        ws.send_json({"type": "user_message", "content": "write me an essay"})

        # Wait for real streaming before stopping — otherwise the test could
        # pass by cancelling a turn that had not started.
        first = ws.receive_json()
        assert first["type"] == "token"

        ws.send_json({"type": "cancel"})
        frames = _receive_until(ws, "turn")

        summary = frames[-1]
        assert summary["cancelled"] is True
        assert "final" not in [frame["type"] for frame in frames]
        assert "error" not in [frame["type"] for frame in frames]
        # The partial answer was streamed and its tokens are accounted for.
        assert summary["completion_tokens"] > 0

    assert llm.cleaned_up, "the cancelled stream must be closed, not leaked"


def test_socket_survives_cancel_and_serves_the_next_turn():
    """Stopping is not a fatal error: the same connection keeps working."""
    app = _slow_app(SlowLLM())
    with TestClient(app) as client, client.websocket_connect("/chat") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": "first"})
        assert ws.receive_json()["type"] == "token"
        ws.send_json({"type": "cancel"})
        assert _receive_until(ws, "turn")[-1]["cancelled"] is True

        ws.send_json({"type": "user_message", "content": "second"})
        assert _receive_until(ws, "turn")[-1]["type"] == "turn"


def test_cancel_with_no_turn_running_is_ignored(client):
    """A stray Stop (double-click, late frame) must not error or close the socket."""
    with client.websocket_connect("/chat") as ws:
        ws.receive_json()
        ws.send_json({"type": "cancel"})
        ws.send_json({"type": "user_message", "content": "still alive?"})
        assert collect_until_final(ws)[-1]["type"] == "final"


def test_second_message_mid_turn_is_refused():
    """One turn at a time — a queued question would answer from stale history."""
    app = _slow_app(SlowLLM())
    with TestClient(app) as client, client.websocket_connect("/chat") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": "first"})
        assert ws.receive_json()["type"] == "token"

        ws.send_json({"type": "user_message", "content": "second"})
        error = _receive_until(ws, "error")[-1]
        assert "stop it first" in error["message"]

        ws.send_json({"type": "cancel"})
        assert _receive_until(ws, "turn")[-1]["cancelled"] is True


def test_cancelled_turn_is_audited_and_keeps_the_partial_answer():
    """The stopped turn is a real record: persisted, and visible to the next turn."""
    app = _slow_app(SlowLLM())
    with TestClient(app) as client, client.websocket_connect("/chat") as ws:
        session_id = ws.receive_json()["session_id"]
        ws.send_json({"type": "user_message", "content": "essay please"})
        assert ws.receive_json()["type"] == "token"
        ws.send_json({"type": "cancel"})
        summary = _receive_until(ws, "turn")[-1]

        record = client.get(f"/api/sessions/{session_id}/turns/{summary['turn_id']}").json()
        assert record["cancelled"] is True

        # Straight from the store the next turn will read, rather than through
        # the transcript endpoint — this asserts what the model will see.
        history = asyncio.run(app.state.session_store.history(session_id))
        replies = [message for message in history if message.role == "assistant"]
        assert replies, "the partial answer must survive as conversation history"
        assert "[stopped by the user]" in replies[-1].content


def test_disconnect_mid_turn_cancels_the_work():
    """Closing the tab must not leave a turn streaming into a dead socket."""
    llm = SlowLLM()
    app = _slow_app(llm)
    # Deliberately nested (not SIM117's single `with`): the socket must close
    # while the app is still up, which is the scenario under test.
    with TestClient(app) as client:  # noqa: SIM117
        with client.websocket_connect("/chat") as ws:
            ws.receive_json()
            ws.send_json({"type": "user_message", "content": "essay please"})
            assert ws.receive_json()["type"] == "token"
        # Leaving the block closes the socket; the app shutdown below only
        # completes if the turn task was actually torn down.
    assert llm.cleaned_up
