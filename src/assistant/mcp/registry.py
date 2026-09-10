"""MCP client registry: connect to servers and adapt their tools.

Each connected server's tools are namespaced (`code__search_code`,
`github__list_pull_requests`) and wrapped as ordinary registry Tools, so the
agent backends can't tell a native tool from an MCP one. A server that fails
to connect is logged and skipped — the agent runs with whatever is reachable
(graceful degradation).
"""

import asyncio
import sys
from contextlib import AsyncExitStack

import httpx2  # the MCP SDK's transport since v2
import structlog
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent
from mcp.types import Tool as MCPToolInfo

from assistant.agent.tools import Tool
from assistant.config import MCPServerConfig

logger = structlog.get_logger("assistant.mcp")

CONNECT_TIMEOUT_S = 15.0
_CALL_TIMEOUT_S = 60


class MCPRegistry:
    def __init__(
        self, configs: list[MCPServerConfig], *, connect_timeout: float = CONNECT_TIMEOUT_S
    ) -> None:
        self._configs = configs
        self._connect_timeout = connect_timeout
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
                server_tools = await asyncio.wait_for(self._connect(config), self._connect_timeout)
            except Exception:
                logger.warning("mcp.server_unavailable", server=config.name, exc_info=True)
                continue
            tools.extend(server_tools)
            self.connected_servers.append(config.name)
            logger.info(
                "mcp.server_connected",
                server=config.name,
                tools=[tool.name for tool in server_tools],
            )
        return tools

    async def _connect(self, config: MCPServerConfig) -> list[Tool]:
        # Everything for one server is entered on its own stack, and handed to
        # the shared one only once the handshake succeeded. Entering it on the
        # shared stack directly meant a server that spawned but never answered
        # `initialize` outlived the connect timeout: the subprocess stayed
        # alive until shutdown, with no tools registered for it.
        async with AsyncExitStack() as stack:
            if config.transport == "stdio":
                if not config.command:
                    raise ValueError(
                        f"MCP server {config.name!r}: stdio transport requires a command"
                    )
                command = sys.executable if config.command == "{python}" else config.command
                env = {**get_default_environment(), **config.env} if config.env else None
                params = StdioServerParameters(command=command, args=config.args, env=env)
                read, write = await stack.enter_async_context(stdio_client(params))
            else:
                if not config.url:
                    raise ValueError(f"MCP server {config.name!r}: http transport requires a url")
                # Auth headers ride on a caller-supplied client; the exit stack closes
                # it with the session, so a failed connect doesn't leak the connection.
                # httpx2, not httpx: the MCP SDK moved its transport there in
                # v2 and type-checks the client it is handed.
                http_client = (
                    await stack.enter_async_context(httpx2.AsyncClient(headers=config.headers))
                    if config.headers
                    else None
                )
                # Two streams since SDK v2 — the session-id callback that used
                # to be third is gone.
                read, write = await stack.enter_async_context(
                    streamable_http_client(config.url, http_client=http_client)
                )
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            listed = await session.list_tools()
            tools = [self._adapt(config.name, info, session) for info in listed.tools]
            # Connected: keep the transport and session open for the app's lifetime.
            self._stack.push_async_callback(stack.pop_all().aclose)
            return tools

    @staticmethod
    def _adapt(server_name: str, info: MCPToolInfo, session: ClientSession) -> Tool:
        async def handler(arguments: dict[str, object]) -> str:
            result = await asyncio.wait_for(
                session.call_tool(info.name, arguments=dict(arguments)), _CALL_TIMEOUT_S
            )
            texts = [item.text for item in result.content if isinstance(item, TextContent)]
            text = "\n".join(texts).strip() or "(empty result)"
            # snake_case since MCP SDK v2; `isError`/`inputSchema` still work
            # but warn, and the wire format is camelCase either way.
            return f"error: {text}" if result.is_error else text

        return Tool(
            name=f"{server_name}__{info.name}",
            description=info.description or f"Tool {info.name} from MCP server {server_name}",
            parameters=dict(info.input_schema or {"type": "object", "properties": {}}),
            handler=handler,
        )

    async def close(self) -> None:
        try:
            await self._stack.aclose()
        except Exception:
            logger.warning("mcp.close_failed", exc_info=True)
