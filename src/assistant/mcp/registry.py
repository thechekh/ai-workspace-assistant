"""MCP client registry: connect to servers and adapt their tools.

Each connected server's tools are namespaced (`code__search_code`,
`github__list_pull_requests`) and wrapped as ordinary registry Tools, so the
agent backends can't tell a native tool from an MCP one. A server that fails
to connect is logged and skipped — the agent runs with whatever is reachable
(graceful degradation).
"""

import asyncio
import logging
import sys
from contextlib import AsyncExitStack

import httpx
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent
from mcp.types import Tool as MCPToolInfo

from assistant.agent.tools import Tool
from assistant.config import MCPServerConfig

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_S = 15
_CALL_TIMEOUT_S = 60


class MCPRegistry:
    def __init__(self, configs: list[MCPServerConfig]) -> None:
        self._configs = configs
        self._stack = AsyncExitStack()
        # Connection bookkeeping so /api/health can tell "no servers configured"
        # apart from "every server failed to connect".
        self.expected_servers: list[str] = [c.name for c in configs if c.enabled]
        self.connected_servers: list[str] = []

    async def start(self) -> list[Tool]:
        """Connect every enabled server; return the adapted tools that are reachable."""
        tools: list[Tool] = []
        for config in self._configs:
            if not config.enabled:
                continue
            try:
                server_tools = await asyncio.wait_for(self._connect(config), _CONNECT_TIMEOUT_S)
            except Exception:
                logger.warning(
                    "MCP server %r unavailable — continuing without its tools",
                    config.name,
                    exc_info=True,
                )
                continue
            tools.extend(server_tools)
            self.connected_servers.append(config.name)
            logger.info(
                "MCP server %r connected: %s", config.name, [tool.name for tool in server_tools]
            )
        return tools

    async def _connect(self, config: MCPServerConfig) -> list[Tool]:
        if config.transport == "stdio":
            if not config.command:
                raise ValueError(f"MCP server {config.name!r}: stdio transport requires a command")
            command = sys.executable if config.command == "{python}" else config.command
            env = {**get_default_environment(), **config.env} if config.env else None
            params = StdioServerParameters(command=command, args=config.args, env=env)
            read, write = await self._stack.enter_async_context(stdio_client(params))
        else:
            if not config.url:
                raise ValueError(f"MCP server {config.name!r}: http transport requires a url")
            # Auth headers ride on a caller-supplied client; the exit stack closes
            # it with the session, so a failed connect doesn't leak the connection.
            http_client = (
                await self._stack.enter_async_context(httpx.AsyncClient(headers=config.headers))
                if config.headers
                else None
            )
            # The HTTP transport also yields a session-id callback we don't use.
            read, write, _ = await self._stack.enter_async_context(
                streamable_http_client(config.url, http_client=http_client)
            )
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        listed = await session.list_tools()
        return [self._adapt(config.name, info, session) for info in listed.tools]

    @staticmethod
    def _adapt(server_name: str, info: MCPToolInfo, session: ClientSession) -> Tool:
        async def handler(arguments: dict[str, object]) -> str:
            result = await asyncio.wait_for(
                session.call_tool(info.name, arguments=dict(arguments)), _CALL_TIMEOUT_S
            )
            texts = [item.text for item in result.content if isinstance(item, TextContent)]
            text = "\n".join(texts).strip() or "(empty result)"
            return f"error: {text}" if result.isError else text

        return Tool(
            name=f"{server_name}__{info.name}",
            description=info.description or f"Tool {info.name} from MCP server {server_name}",
            parameters=dict(info.inputSchema or {"type": "object", "properties": {}}),
            handler=handler,
        )

    async def close(self) -> None:
        try:
            await self._stack.aclose()
        except Exception:
            logger.warning("error while closing MCP connections", exc_info=True)
