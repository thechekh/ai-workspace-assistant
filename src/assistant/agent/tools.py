"""Shared tool registry — every agent backend gets the same tools.

Native tools live here (search_docs over the RAG retriever); MCP-provided
tools are adapted into this same registry in Phase 4.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from assistant.llm.client import ToolSpec
from assistant.rag.retriever import Retriever

ToolHandler = Callable[[dict[str, object]], Awaitable[str]]

_MAX_CHUNK_CHARS = 1500


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, object]  # JSON schema for the arguments
    handler: ToolHandler

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, parameters=self.parameters)


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {tool.name: tool for tool in tools or []}

    def __len__(self) -> int:
        return len(self._tools)

    @property
    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    @property
    def specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    async def execute(self, name: str, arguments: dict[str, object]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"error: unknown tool {name!r}"
        try:
            return await tool.handler(arguments)
        except Exception as exc:  # a tool crash must not kill the agent loop
            return f"error: tool {name!r} failed: {exc}"


def make_search_docs(retriever: Retriever, *, limit: int = 4) -> Tool:
    async def handler(arguments: dict[str, object]) -> str:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return "error: the 'query' argument is required"
        results = await retriever.search(query, limit=limit)
        if not results:
            return "No matching documents found."
        blocks: list[str] = []
        for result in results:
            text = result.text
            if len(text) > _MAX_CHUNK_CHARS:
                text = text[:_MAX_CHUNK_CHARS] + "…"
            blocks.append(
                f"[{result.source} — {result.heading}] (score {result.score:.2f})\n{text}"
            )
        return "\n\n---\n\n".join(blocks)

    return Tool(
        name="search_docs",
        description=(
            "Search the internal engineering documentation: architecture, service "
            "catalog, deployment, coding guidelines, incident response, onboarding. "
            "Call this whenever the user asks about our systems, services, or processes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language search query"}
            },
            "required": ["query"],
        },
        handler=handler,
    )
