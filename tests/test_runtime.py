"""Startup and shutdown of the application graph (`main.build_runtime` / `Runtime.aclose`).

The lifespan is where resources are acquired and released: these pin that a
failure halfway through startup releases what was already opened, and that
one failing close does not skip the ones after it.
"""

import pytest
from fakeredis import FakeAsyncRedis

from assistant.main import (
    REDIS_CONNECT_TIMEOUT_S,
    REDIS_SOCKET_TIMEOUT_S,
    _redis_from_url,
    build_runtime,
)
from assistant.rag.embeddings import HashEmbedder
from tests.conftest import HermeticSettings, build_seeded_retriever_async


def test_redis_client_has_socket_timeouts():
    """A wedged Redis must fail loudly, not hang the rate limiter and every write."""
    client = _redis_from_url("redis://localhost:6379/0")
    kwargs = client.connection_pool.connection_kwargs
    assert kwargs["socket_connect_timeout"] == REDIS_CONNECT_TIMEOUT_S
    assert kwargs["socket_timeout"] == REDIS_SOCKET_TIMEOUT_S


async def test_runtime_shares_one_embedder_between_retriever_and_ingestion():
    retriever = await build_seeded_retriever_async()
    runtime = await build_runtime(
        HermeticSettings(mcp_enabled=False),
        redis_client=FakeAsyncRedis(decode_responses=True),
        retriever=retriever,
    )
    try:
        assert runtime.embedder is retriever.embedder
        assert isinstance(runtime.embedder, HashEmbedder)
    finally:
        await runtime.aclose()


async def test_startup_failure_releases_what_was_already_opened(monkeypatch):
    """MCP servers are spawned before the agents are built: if building the
    agents fails, the subprocesses must not outlive the failed startup."""
    closed: list[str] = []

    class FakeRegistry:
        def __init__(self, configs, **kwargs):
            self.expected_servers = [config.name for config in configs]
            self.connected_servers: list[str] = []

        async def start(self):
            return []

        async def close(self):
            closed.append("mcp")

    def explode(*args, **kwargs):
        raise RuntimeError("no agents today")

    monkeypatch.setattr("assistant.main.MCPRegistry", FakeRegistry)
    monkeypatch.setattr("assistant.main.build_agents", explode)

    with pytest.raises(RuntimeError, match="no agents today"):
        await build_runtime(
            HermeticSettings(mcp_enabled=True),
            redis_client=FakeAsyncRedis(decode_responses=True),
            retriever=await build_seeded_retriever_async(),
        )
    assert closed == ["mcp"]


async def test_aclose_keeps_going_after_a_failing_step():
    """Best-effort means every resource gets its close call, whatever the first one did."""
    runtime = await build_runtime(
        HermeticSettings(mcp_enabled=False),
        redis_client=FakeAsyncRedis(decode_responses=True),
        retriever=await build_seeded_retriever_async(),
    )

    class BrokenRegistry:
        async def close(self):
            raise RuntimeError("stdio already gone")

    class Recorder:
        closed = False

        async def aclose(self):
            self.closed = True

    http = Recorder()
    runtime.mcp_registry = BrokenRegistry()  # type: ignore[assignment]
    runtime.http_client = http  # type: ignore[assignment]

    await runtime.aclose()  # must not raise
    assert http.closed, "the HTTP pool must be released even though MCP failed to close"
