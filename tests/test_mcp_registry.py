"""The MCP registry's connect path, without subprocesses.

`test_mcp.py` spawns the bundled servers; this file fakes the transport so a
server that never answers the handshake can be simulated in milliseconds.
"""

from contextlib import asynccontextmanager

import anyio

from assistant.config import MCPServerConfig
from assistant.mcp.registry import MCPRegistry


async def test_a_server_that_never_answers_is_torn_down_at_the_timeout(monkeypatch):
    """Found with a stuck server: the connect timeout fired, no tools were
    registered — and the subprocess stayed alive until shutdown, because its
    transport had already been handed to the app-lifetime exit stack."""
    state = {"entered": False, "exited": False}

    @asynccontextmanager
    async def silent_stdio(params):
        # Streams the session can write to, and a read side nobody feeds:
        # `initialize()` waits for an answer that never comes.
        client_read_send, client_read = anyio.create_memory_object_stream(64)
        client_write, server_read = anyio.create_memory_object_stream(64)
        state["entered"] = True
        try:
            yield client_read, client_write
        finally:
            state["exited"] = True
            await client_read_send.aclose()
            await server_read.aclose()

    monkeypatch.setattr("assistant.mcp.registry.stdio_client", silent_stdio)

    registry = MCPRegistry(
        [MCPServerConfig(name="stuck", command="{python}", args=["-m", "nothing"])],
        connect_timeout=0.3,
    )
    tools = await registry.start()
    assert tools == []
    assert registry.connected_servers == []
    assert state["entered"]
    assert state["exited"], "the transport must be closed when the connect times out"
    await registry.close()
