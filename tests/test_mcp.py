"""MCP integration tests — spawn the real bundled stdio servers (no credentials).

Marked `slow`: these fork subprocesses. Skip them with `pytest -m "not slow"`.
"""

import pytest

from assistant.config import MCPServerConfig
from assistant.mcp.registry import MCPRegistry

pytestmark = pytest.mark.slow


def _stdio(name: str, module: str) -> MCPServerConfig:
    return MCPServerConfig(name=name, command="{python}", args=["-m", module])


async def test_stdio_servers_expose_namespaced_tools_and_execute():
    registry = MCPRegistry(
        [
            _stdio("code", "assistant.mcp_servers.code_search"),
            _stdio("github", "assistant.mcp_servers.fake_github"),
        ]
    )
    tools = await registry.start()
    try:
        names = {tool.name for tool in tools}
        assert {"code__search_code", "code__read_file", "github__list_pull_requests"} <= names

        # code search over this very repository
        search = next(tool for tool in tools if tool.name == "code__search_code")
        result = await search.handler({"pattern": "class CustomAgent"})
        assert "custom.py" in result

        # path traversal is rejected
        read = next(tool for tool in tools if tool.name == "code__read_file")
        guarded = await read.handler({"path": "../outside.txt"})
        assert guarded.startswith("error:")

        # mocked GitHub data flows through the same adapter
        prs = next(tool for tool in tools if tool.name == "github__list_pull_requests")
        listing = await prs.handler({"state": "open"})
        assert "#142" in listing
        assert "LangGraph backend" in listing
    finally:
        await registry.close()


async def test_unreachable_server_degrades_gracefully():
    registry = MCPRegistry(
        [MCPServerConfig(name="broken", command="definitely-not-a-real-binary-xyz")]
    )
    tools = await registry.start()  # must not raise
    await registry.close()
    assert tools == []


async def test_disabled_server_is_skipped():
    registry = MCPRegistry(
        [
            MCPServerConfig(
                name="github",
                command="{python}",
                args=["-m", "assistant.mcp_servers.fake_github"],
                enabled=False,
            )
        ]
    )
    tools = await registry.start()
    await registry.close()
    assert tools == []


async def test_http_transport_sends_auth_headers(monkeypatch):
    """A remote server's credentials must reach the wire.

    GitHub's hosted MCP server authenticates with `Authorization: Bearer <PAT>`,
    so `headers` has to arrive as an httpx client on the transport — without
    this the http transport can only ever talk to unauthenticated servers.
    """
    captured: dict[str, object] = {}

    def fake_transport(url: str, *, http_client=None, **kwargs):
        captured["url"] = url
        captured["headers"] = dict(http_client.headers) if http_client else None
        raise RuntimeError("stop here — the handshake is not what is under test")

    monkeypatch.setattr("assistant.mcp.registry.streamable_http_client", fake_transport)

    registry = MCPRegistry(
        [
            MCPServerConfig(
                name="github",
                transport="http",
                url="https://api.githubcopilot.com/mcp/",
                headers={"Authorization": "Bearer ghp_example"},
            )
        ]
    )
    tools = await registry.start()  # degrades gracefully past the RuntimeError
    await registry.close()

    assert tools == []
    assert captured["url"] == "https://api.githubcopilot.com/mcp/"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers.get("authorization") == "Bearer ghp_example"


async def test_http_transport_without_headers_passes_no_client(monkeypatch):
    """No headers configured = no bespoke client, so the SDK keeps its defaults."""
    captured: dict[str, object] = {}

    def fake_transport(url: str, *, http_client=None, **kwargs):
        captured["http_client"] = http_client
        raise RuntimeError("stop here")

    monkeypatch.setattr("assistant.mcp.registry.streamable_http_client", fake_transport)

    registry = MCPRegistry(
        [MCPServerConfig(name="plain", transport="http", url="http://localhost:9999/mcp")]
    )
    await registry.start()
    await registry.close()

    assert captured["http_client"] is None
