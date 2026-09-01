"""Regressions found by a full-project review, each reproduced before its fix.

Grouped here rather than scattered because they share a lesson worth keeping
visible: every one of them passed the existing suite. They were found by
asking "what does this do with the input nobody sends on purpose?" — a
document that got shorter, a page that redirects, `?limit=0`.
"""

import asyncio
import hashlib
import json

import httpx
import pytest
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient
from prometheus_client import generate_latest
from pydantic import SecretStr
from qdrant_client import AsyncQdrantClient

from assistant.agent.base import ChatMessage
from assistant.agent.tools import ToolRegistry
from assistant.agent.tools.fetch import is_blocked_host, make_fetch_url
from assistant.llm.client import FakeLLM
from assistant.main import create_app
from assistant.memory.session import SessionStore
from assistant.rag.embeddings import build_embedder
from assistant.rag.ingest import ingest_documents
from assistant.rag.retriever import Retriever
from assistant.rag.store import VectorStore
from tests.conftest import HermeticSettings, build_seeded_retriever

# --- Re-ingest must replace, not accumulate ---------------------------------

_LONG = (
    "# Runbook\n\n## Rollback\n\nRun deployctl rollback.\n\n"
    "## Decommissioned procedure\n\nThe old procedure is xyzzy-classified.\n\n"
    "## Escalation\n\nPage the on-call engineer.\n"
)
_SHORT = "# Runbook\n\n## Rollback\n\nRun deployctl rollback.\n"


@pytest.fixture
async def store() -> VectorStore:
    return VectorStore(AsyncQdrantClient(":memory:"), "regressions")


async def test_reuploading_a_shorter_document_removes_the_dropped_text(
    store: VectorStore,
) -> None:
    """Chunk ids are uuid5(source, heading, index), so a removed section keeps
    an id nothing new collides with. Overwriting is not replacing: without an
    explicit delete the deleted paragraph stays indexed and citable."""
    settings = HermeticSettings()
    await ingest_documents([("runbook.md", _LONG)], settings, store=store)
    await ingest_documents([("runbook.md", _SHORT)], settings, store=store)

    assert dict(await store.list_sources()) == {"runbook.md": 1}

    retriever = Retriever(build_embedder(settings), store, mode="hybrid")
    hits = await retriever.search("decommissioned procedure xyzzy", limit=5)
    assert not [hit for hit in hits if "xyzzy" in hit.text], (
        "deleted content is still retrievable — the assistant would cite it"
    )


async def test_editing_a_heading_does_not_leave_the_old_copy(store: VectorStore) -> None:
    """The id includes the breadcrumb, so a renamed heading orphans its chunk."""
    settings = HermeticSettings()
    await ingest_documents(
        [("faq.md", "# FAQ\n\n## Old title\n\nThe answer is 42.\n")], settings, store=store
    )
    await ingest_documents(
        [("faq.md", "# FAQ\n\n## New title\n\nThe answer is 42.\n")], settings, store=store
    )

    assert dict(await store.list_sources()) == {"faq.md": 1}


async def test_other_documents_survive_a_reupload(store: VectorStore) -> None:
    """Replacing one source must not touch the others sharing the collection."""
    settings = HermeticSettings()
    await ingest_documents(
        [("keep.md", "# Keep\n\nImportant.\n"), ("replace.md", _LONG)], settings, store=store
    )
    await ingest_documents([("replace.md", _SHORT)], settings, store=store)

    assert dict(await store.list_sources()) == {"keep.md": 1, "replace.md": 1}


# --- Session listing: the cap must not invert -------------------------------


async def test_a_zero_or_negative_limit_returns_nothing_not_everything() -> None:
    """ZREVRANGE's end index is inclusive and negatives count from the end, so
    limit=0 asked for 0..-1 — the whole set. A cap that returns more than the
    default when you ask for less is worse than no cap."""
    store = SessionStore(FakeAsyncRedis(decode_responses=True), ttl_seconds=3600)
    for index in range(12):
        await store.append(f"s{index}", ChatMessage(role="user", content=f"q{index}"))

    assert await store.recent(limit=0) == []
    assert await store.recent(limit=-5) == []
    assert len(await store.recent(limit=3)) == 3


def test_the_endpoint_clamps_a_hostile_limit() -> None:
    settings = HermeticSettings(llm_provider="fake", agent_backend="custom", mcp_enabled=False)
    app = create_app(
        settings,
        redis_client=FakeAsyncRedis(decode_responses=True),
        llm=FakeLLM(),
        retriever=build_seeded_retriever(),
    )
    with TestClient(app) as client:
        for hostile in ("0", "-1", "999999"):
            response = client.get(f"/api/sessions?limit={hostile}")
            assert response.status_code == 200
            assert len(response.json()["sessions"]) <= 100


# --- Metric cardinality -----------------------------------------------------


async def test_an_invented_tool_name_never_becomes_a_metric_label() -> None:
    """Tool names in a call come from the model. Labelling a counter with one
    lets a hallucination add a time series that never goes away."""
    registry = ToolRegistry()
    result = await registry.execute("tool_the_model_dreamt_up", {})
    assert result.startswith("error: unknown tool")

    exposed = generate_latest().decode()
    assert "tool_the_model_dreamt_up" not in exposed
    assert 'tool="<unregistered>"' in exposed


# --- SSRF: the guard has to survive a redirect ------------------------------


@pytest.mark.parametrize(
    "host",
    ["localhost", "localhost.", "127.0.0.1", "10.1.2.3", "192.168.0.1", "169.254.169.254", "::1"],
)
def test_internal_hosts_are_recognised(host: str) -> None:
    assert is_blocked_host(host)


@pytest.mark.parametrize("host", ["example.com", "api.github.com", "10x.dev", "1270.0.0.1"])
def test_public_hosts_are_not(host: str) -> None:
    assert not is_blocked_host(host)


async def test_a_redirect_to_localhost_is_refused() -> None:
    """The classic bypass: a public URL passes the check, then 302s inward.
    169.254.169.254 is the cloud metadata address this protects in practice."""
    hops = {
        "https://public.example/start": httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
        ),
        "http://169.254.169.254/latest/meta-data/": httpx.Response(
            200, text="iam/security-credentials/admin"
        ),
    }

    def transport(request: httpx.Request) -> httpx.Response:
        return hops[str(request.url)]

    from assistant.agent.tools.fetch import _refuse_internal_redirects

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport),
        follow_redirects=True,
        event_hooks={"response": [_refuse_internal_redirects]},
    ) as client:
        tool = make_fetch_url(client=client)
        result = await tool.run({"url": "https://public.example/start"})

    assert result.startswith("error:")
    assert "security-credentials" not in result


# --- Auth: constant-time comparison, and no secret in a Redis key -----------


def test_auth_still_accepts_the_right_token_and_rejects_the_wrong_one() -> None:
    """compare_digest changed the comparison; it must not change the outcome."""
    settings = HermeticSettings(
        llm_provider="fake",
        agent_backend="custom",
        mcp_enabled=False,
        auth_token=SecretStr("s3cret"),
    )
    app = create_app(
        settings,
        redis_client=FakeAsyncRedis(decode_responses=True),
        llm=FakeLLM(),
        retriever=build_seeded_retriever(),
    )
    with TestClient(app) as client:
        assert client.get("/api/sessions").status_code == 401
        assert (
            client.get("/api/sessions", headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )
        # A prefix of the real token must not pass either.
        assert (
            client.get("/api/sessions", headers={"Authorization": "Bearer s3c"}).status_code == 401
        )
        assert (
            client.get("/api/sessions", headers={"Authorization": "Bearer s3cret"}).status_code
            == 200
        )

        with client.websocket_connect("/chat?token=s3cret") as ws:
            assert ws.receive_json()["type"] == "session"


def test_the_rate_limit_key_does_not_contain_the_token() -> None:
    """The identity ends up in a Redis key name, which is dumpable with KEYS
    and lands in slowlogs and RDB snapshots — so it is hashed, not sliced."""
    redis = FakeAsyncRedis(decode_responses=True)
    token = "super-secret-token-value"
    settings = HermeticSettings(
        llm_provider="fake",
        agent_backend="custom",
        mcp_enabled=False,
        auth_token=SecretStr(token),
    )
    app = create_app(
        settings, redis_client=redis, llm=FakeLLM(), retriever=build_seeded_retriever()
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/documents",
            data={"text": "# Doc\n\nbody\n", "source": "a.md"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

    async def _keys() -> list[str]:
        return [str(key) for key in await redis.keys("ratelimit:*")]

    keys = asyncio.run(_keys())
    assert keys, "the write limiter should have recorded the request"
    joined = " ".join(keys)
    assert token not in joined
    assert token[-16:] not in joined
    assert hashlib.sha256(f"Bearer {token}".encode()).hexdigest()[:16] in joined


# --- Every turn ends with exactly one `turn` frame --------------------------


class ExplodingLLM:
    """Fails the way a provider does: after streaming part of an answer."""

    async def stream_step(self, messages, tools=None):
        from assistant.llm.client import TextDelta

        yield TextDelta(text="here is the start of an ans")
        raise RuntimeError("provider fell over mid-stream")


def test_a_failed_turn_still_reports_what_it_cost() -> None:
    """The error frame says what went wrong; the `turn` frame says what it cost.

    Found live: a provider's `tool_use_failed` burned three prompts across two
    retries and then returned early, so the turn never reached the summary —
    no `turn` frame (clients waiting for one hang), no audit row, and the spend
    missing from cost metrics at exactly the moment spend matters.
    """
    settings = HermeticSettings(llm_provider="fake", agent_backend="custom", mcp_enabled=False)
    app = create_app(
        settings,
        redis_client=FakeAsyncRedis(decode_responses=True),
        llm=ExplodingLLM(),  # pyright: ignore[reportArgumentType]
        retriever=build_seeded_retriever(),
    )
    with TestClient(app) as client, client.websocket_connect("/chat") as ws:
        session_id = ws.receive_json()["session_id"]
        ws.send_json({"type": "user_message", "content": "anything"})

        frames = []
        for _ in range(50):
            frames.append(ws.receive_json())
            if frames[-1]["type"] == "turn":
                break

        kinds = [frame["type"] for frame in frames]
        assert "error" in kinds, kinds
        assert kinds[-1] == "turn", f"the turn frame must terminate every turn: {kinds}"

        summary = frames[-1]
        assert summary["failed"] is True
        assert summary["cancelled"] is False
        assert summary["completion_tokens"] > 0, "the tokens spent before the failure are real"

        # And the socket is still usable afterwards.
        ws.send_json({"type": "cancel"})

    record = client.get(f"/api/sessions/{session_id}/turns").json()["turns"][-1]
    assert record["failed"] is True, "a failed turn must still be auditable"


def test_the_partial_answer_of_a_failed_turn_survives_as_history() -> None:
    """What the user saw is part of the conversation, however the turn ended."""
    settings = HermeticSettings(llm_provider="fake", agent_backend="custom", mcp_enabled=False)
    redis = FakeAsyncRedis(decode_responses=True)
    app = create_app(
        settings,
        redis_client=redis,
        llm=ExplodingLLM(),  # pyright: ignore[reportArgumentType]
        retriever=build_seeded_retriever(),
    )
    with TestClient(app) as client:
        with client.websocket_connect("/chat") as ws:
            session_id = ws.receive_json()["session_id"]
            ws.send_json({"type": "user_message", "content": "anything"})
            for _ in range(50):
                if ws.receive_json()["type"] == "turn":
                    break

        messages = client.get(f"/api/sessions/{session_id}/messages").json()["messages"]
        replies = [m for m in messages if m["role"] == "assistant"]
        assert replies, "the text the user already saw must not vanish"
        assert "[answer interrupted]" in replies[-1]["content"]


# --- Leaked tool-call markup: all four openers seen in the wild -------------


@pytest.mark.parametrize(
    "leaked",
    [
        '<function=search_docs>{"query": "rollback"}',
        '<function.search_docs>{"query": "rollback"}</function>',
        '<function(search_docs){"query": "rollback"}',
        # Seen live on a llama model: an opening paren, not an angle bracket.
        # Missing it put raw markup in front of a user mid-answer.
        '(function=search_docs>{"query": "rollback"}',
    ],
)
def test_every_observed_leak_syntax_is_recovered(leaked: str) -> None:
    from assistant.llm.client import parse_leaked_tool_calls

    calls = parse_leaked_tool_calls(leaked)
    assert calls, f"{leaked!r} would reach the user as raw markup"
    assert calls[0].name == "search_docs"
    assert json.loads(calls[0].arguments) == {"query": "rollback"}


def test_ordinary_prose_is_not_withheld() -> None:
    """The buffer must not hold back a normal answer waiting for markup."""
    from assistant.llm.client import _LeakedTextBuffer

    buffer = _LeakedTextBuffer()
    assert buffer.push("To roll back a release") == "To roll back a release"
    assert buffer.push(", run deployctl.") == ", run deployctl."


def test_a_sentence_starting_like_the_markup_still_streams() -> None:
    """ "(function" is held only until it is provably not the prefix."""
    from assistant.llm.client import _LeakedTextBuffer

    buffer = _LeakedTextBuffer()
    assert buffer.push("(fun") is None  # could still become "(function..."
    assert buffer.push("ctional programming is nice)") == "(functional programming is nice)"
