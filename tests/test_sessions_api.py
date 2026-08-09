"""Listing, switching and deleting conversations.

The sidebar is only useful if the index tells the truth: a session that
expired must not be listed, order must be by real activity, and deleting one
must take its audit trail with it.
"""

import asyncio

import pytest
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient
from pydantic import SecretStr

from assistant.agent.base import ChatMessage
from assistant.api.schemas import TurnRecord
from assistant.llm.client import FakeLLM
from assistant.main import create_app
from assistant.memory.session import SessionStore
from tests.conftest import HermeticSettings, build_seeded_retriever, collect_until_final


@pytest.fixture
def store() -> SessionStore:
    return SessionStore(FakeAsyncRedis(decode_responses=True), ttl_seconds=3600)


async def test_recent_lists_newest_first_with_a_preview(store: SessionStore) -> None:
    for session_id, question in (("s1", "how do I deploy?"), ("s2", "where are the runbooks?")):
        await store.append(session_id, ChatMessage(role="user", content=question))
        await store.append(session_id, ChatMessage(role="assistant", content="an answer"))
        await asyncio.sleep(0.01)  # distinct activity timestamps

    sessions = await store.recent()
    assert [item.session_id for item in sessions] == ["s2", "s1"]
    assert sessions[0].preview == "where are the runbooks?"
    assert sessions[0].messages == 2


async def test_activity_reorders_the_list(store: SessionStore) -> None:
    await store.append("older", ChatMessage(role="user", content="first"))
    await asyncio.sleep(0.01)
    await store.append("newer", ChatMessage(role="user", content="second"))
    assert [item.session_id for item in await store.recent()] == ["newer", "older"]

    await asyncio.sleep(0.01)
    await store.append("older", ChatMessage(role="user", content="back again"))
    assert [item.session_id for item in await store.recent()] == ["older", "newer"]


async def test_long_previews_are_truncated(store: SessionStore) -> None:
    await store.append("s1", ChatMessage(role="user", content="q" * 500))
    [session] = await store.recent()
    assert 0 < len(session.preview) <= 80


async def test_expired_sessions_are_not_listed() -> None:
    """Redis expires keys, not sorted-set members — the index must not lie."""
    redis = FakeAsyncRedis(decode_responses=True)
    store = SessionStore(redis, ttl_seconds=3600)
    await store.append("gone", ChatMessage(role="user", content="hello"))
    assert len(await store.recent()) == 1

    await redis.delete(SessionStore._key("gone"))  # simulate the TTL firing
    assert await store.recent() == []


async def test_limit_is_respected(store: SessionStore) -> None:
    for index in range(10):
        await store.append(f"s{index}", ChatMessage(role="user", content=f"q{index}"))
    assert len(await store.recent(limit=3)) == 3


async def test_forget_removes_history_audit_and_index(store: SessionStore) -> None:
    await store.append("s1", ChatMessage(role="user", content="hello"))
    await store.append_turn(
        "s1", TurnRecord(turn_id="t1", backend="custom", duration_ms=5, llm_steps=1)
    )
    await store.set_summary("s1", "a summary", covered=2)

    assert await store.forget("s1") is True
    assert await store.history("s1") == []
    assert await store.turns("s1") == []
    assert await store.summary("s1") == ("", 0)
    assert await store.recent() == []
    assert await store.forget("s1") is False  # already gone


def _client(**overrides: object) -> TestClient:
    settings = HermeticSettings(
        llm_provider="fake",
        agent_backend="custom",
        mcp_enabled=False,
        **overrides,  # pyright: ignore[reportArgumentType]
    )
    return TestClient(
        create_app(
            settings,
            redis_client=FakeAsyncRedis(decode_responses=True),
            llm=FakeLLM(),
            retriever=build_seeded_retriever(),
        )
    )


def test_a_real_chat_shows_up_in_the_listing() -> None:
    with _client() as client:
        assert client.get("/api/sessions").json()["sessions"] == []

        with client.websocket_connect("/chat") as ws:
            session_id = ws.receive_json()["session_id"]
            ws.send_json({"type": "user_message", "content": "which service bills customers?"})
            collect_until_final(ws)

        [session] = client.get("/api/sessions").json()["sessions"]
        assert session["session_id"] == session_id
        assert session["preview"].startswith("which service bills")
        assert session["messages"] >= 2


def test_deleting_a_session_removes_it_from_the_listing() -> None:
    with _client() as client:
        with client.websocket_connect("/chat") as ws:
            session_id = ws.receive_json()["session_id"]
            ws.send_json({"type": "user_message", "content": "hello"})
            collect_until_final(ws)

        assert client.delete(f"/api/sessions/{session_id}").status_code == 200
        assert client.get("/api/sessions").json()["sessions"] == []
        assert client.delete(f"/api/sessions/{session_id}").status_code == 404


def test_reopening_a_session_returns_its_transcript() -> None:
    """The WS resumes history for the model; this is how the UI gets it back."""
    with _client() as client:
        with client.websocket_connect("/chat") as ws:
            session_id = ws.receive_json()["session_id"]
            ws.send_json({"type": "user_message", "content": "which service bills customers?"})
            collect_until_final(ws)

        payload = client.get(f"/api/sessions/{session_id}/messages").json()
        roles = [message["role"] for message in payload["messages"]]
        assert roles[0] == "user"
        assert "assistant" in roles
        assert payload["messages"][0]["content"] == "which service bills customers?"

        # An unknown session is empty, not an error: the id may simply have expired.
        assert client.get("/api/sessions/nope/messages").json()["messages"] == []


def test_listing_previews_require_the_token_when_one_is_set() -> None:
    """Previews are conversation content — unlike /api/info and /api/health."""
    with _client(auth_token=SecretStr("s3cret")) as client:
        assert client.get("/api/sessions").status_code == 401
        assert client.get("/api/sessions/any/messages").status_code == 401
        authorised = client.get("/api/sessions", headers={"Authorization": "Bearer s3cret"})
        assert authorised.status_code == 200
