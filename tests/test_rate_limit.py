"""The rate limiter, and both surfaces that enforce it.

The limiter is a budget guard: its job is to make a runaway client cheap to
refuse. These tests pin the three properties that matter for that — the limit
actually binds, buckets and callers are isolated, and being refused does not
push your own reset further away.
"""

import pytest
from fakeredis import FakeAsyncRedis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistant.api.rate_limit import RateLimiter
from assistant.llm.client import FakeLLM
from assistant.main import create_app
from tests.conftest import HermeticSettings, build_seeded_retriever


@pytest.fixture
def limiter() -> RateLimiter:
    return RateLimiter(FakeAsyncRedis(decode_responses=True))


async def test_allows_up_to_the_limit_then_refuses(limiter: RateLimiter):
    for _ in range(3):
        assert (await limiter.check("turns", "s1", limit=3, window_seconds=60)).allowed

    refused = await limiter.check("turns", "s1", limit=3, window_seconds=60)
    assert not refused.allowed
    assert 0 < refused.retry_after <= 60
    assert "rate limit reached" in refused.message("messages")


async def test_refusal_does_not_extend_the_window(limiter: RateLimiter):
    """A client that keeps hammering must not push its own reset away.

    Rejected requests are removed from the log again, so `retry_after` counts
    down from the *oldest allowed* request, not the most recent attempt.
    """
    for _ in range(2):
        await limiter.check("turns", "s1", limit=2, window_seconds=5)

    first = await limiter.check("turns", "s1", limit=2, window_seconds=5)
    latest = first
    for _ in range(20):
        latest = await limiter.check("turns", "s1", limit=2, window_seconds=5)
    assert latest.retry_after <= first.retry_after


async def test_buckets_and_callers_are_independent(limiter: RateLimiter):
    await limiter.check("turns", "s1", limit=1, window_seconds=60)
    assert not (await limiter.check("turns", "s1", limit=1, window_seconds=60)).allowed
    # A different session, and a different bucket for the same session, are
    # both unaffected — one noisy client cannot lock out everyone else.
    assert (await limiter.check("turns", "s2", limit=1, window_seconds=60)).allowed
    assert (await limiter.check("writes", "s1", limit=1, window_seconds=60)).allowed


async def test_disabled_limiter_and_zero_limit_always_allow():
    off = RateLimiter(FakeAsyncRedis(decode_responses=True), enabled=False)
    for _ in range(50):
        assert (await off.check("turns", "s1", limit=1, window_seconds=60)).allowed

    on = RateLimiter(FakeAsyncRedis(decode_responses=True))
    for _ in range(50):
        # limit=0 turns off one bucket without disabling the whole feature.
        assert (await on.check("turns", "s1", limit=0, window_seconds=60)).allowed


async def test_reset_clears_history(limiter: RateLimiter):
    await limiter.check("turns", "s1", limit=1, window_seconds=60)
    assert not (await limiter.check("turns", "s1", limit=1, window_seconds=60)).allowed

    await limiter.reset("turns", "s1")
    assert (await limiter.check("turns", "s1", limit=1, window_seconds=60)).allowed


def _app(
    *,
    turns_per_minute: int = 20,
    uploads_per_hour: int = 50,
    enabled: bool = True,
) -> FastAPI:
    settings = HermeticSettings(
        llm_provider="fake",
        agent_backend="custom",
        mcp_enabled=False,
        rate_limit_enabled=enabled,
        rate_limit_turns_per_minute=turns_per_minute,
        rate_limit_uploads_per_hour=uploads_per_hour,
    )
    return create_app(
        settings,
        redis_client=FakeAsyncRedis(decode_responses=True),
        llm=FakeLLM(),
        retriever=build_seeded_retriever(),
    )


def test_chat_turns_are_limited_per_session():
    """Over the limit, the client gets an error frame — not an LLM call."""
    app = _app(turns_per_minute=2)
    with TestClient(app) as client, client.websocket_connect("/chat") as ws:
        ws.receive_json()  # session frame

        for _ in range(2):
            ws.send_json({"type": "user_message", "content": "hello"})
            while ws.receive_json()["type"] != "turn":
                pass

        ws.send_json({"type": "user_message", "content": "one too many"})
        refused = ws.receive_json()
        assert refused["type"] == "error"
        assert "rate limit reached" in refused["message"]

        # The socket stays usable — the limit is a delay, not a disconnect.
        ws.send_json({"type": "cancel"})


def test_a_second_session_is_not_affected_by_the_first():
    app = _app(turns_per_minute=1)
    # Nested deliberately (not SIM117): the first socket must be closed before
    # the second connects, so the two sessions never overlap.
    with TestClient(app) as client:
        with client.websocket_connect("/chat") as first:
            first.receive_json()
            first.send_json({"type": "user_message", "content": "hello"})
            while first.receive_json()["type"] != "turn":
                pass
            first.send_json({"type": "user_message", "content": "again"})
            assert first.receive_json()["type"] == "error"

        with client.websocket_connect("/chat") as second:
            second.receive_json()  # a fresh session id -> its own bucket
            second.send_json({"type": "user_message", "content": "hello"})
            assert any(second.receive_json()["type"] == "final" for _ in range(50))


def test_uploads_are_limited_and_report_retry_after():
    app = _app(uploads_per_hour=1)
    with TestClient(app) as client:
        first = client.post("/api/documents", data={"text": "# One", "source": "a.md"})
        assert first.status_code == 200

        second = client.post("/api/documents", data={"text": "# Two", "source": "b.md"})
        assert second.status_code == 429
        assert int(second.headers["Retry-After"]) > 0
        assert "rate limit reached" in second.json()["detail"]


def test_reads_are_never_rate_limited():
    """Health and info back the UI's status dot — throttling them would make a
    busy instance look down."""
    app = _app(uploads_per_hour=1)
    with TestClient(app) as client:
        for _ in range(10):
            assert client.get("/api/health").status_code == 200
            assert client.get("/api/info").status_code == 200


def test_cleanup_still_works_after_hitting_the_upload_limit():
    """The remedy must not be throttled with the thing it remedies.

    Found live: the limiter refused the DELETE calls cleaning up a burst of
    uploads, which would lock someone out of fixing their own mistake for an
    hour. Deleting costs no embedding work, so it is not limited.
    """
    app = _app(uploads_per_hour=1)
    with TestClient(app) as client:
        assert (
            client.post("/api/documents", data={"text": "# One", "source": "a.md"}).status_code
            == 200
        )
        assert (
            client.post("/api/documents", data={"text": "# Two", "source": "b.md"}).status_code
            == 429
        )

        for _ in range(5):
            assert client.delete("/api/documents/a.md").status_code in (200, 404)


def test_disabling_the_feature_restores_unlimited_use():
    app = _app(enabled=False, uploads_per_hour=1)
    with TestClient(app) as client:
        for i in range(5):
            response = client.post("/api/documents", data={"text": "# Doc", "source": f"{i}.md"})
            assert response.status_code == 200
