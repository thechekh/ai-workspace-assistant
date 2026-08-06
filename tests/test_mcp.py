"""MCP integration tests — spawn the real bundled stdio servers (no credentials)."""

from assistant.config import MCPServerConfig
from assistant.mcp.registry import MCPRegistry


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
