"""The `search_docs` tool — RAG over the internal documentation."""

from assistant.agent.tools.base import Tool
from assistant.rag.retriever import Retriever

_MAX_CHUNK_CHARS = 1500

NO_RELEVANT_DOCS = (
    "No relevant documents found in the internal docs. They only cover: "
    "architecture, service catalog, deployment, coding guidelines, incident "
    "response, onboarding. Do not retry with a rephrased query — tell the "
    "user the internal docs do not cover this topic."
)


def make_search_docs(retriever: Retriever, *, limit: int = 4) -> Tool:
    async def handler(arguments: dict[str, object]) -> str:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return "error: the 'query' argument is required"
        # The retriever applies the relevance gate itself, so an empty list
        # here genuinely means "nothing relevant", not "nothing indexed".
        results = await retriever.search(query, limit=limit)
        if not results:
            return NO_RELEVANT_DOCS
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
            "Search the INTERNAL engineering documentation only: architecture, "
            "service catalog, deployment, coding guidelines, incident response, "
            "onboarding. Call this whenever the user asks about our systems, "
            "services, or processes. It knows nothing about external websites, "
            "GitHub repositories, or other companies — use fetch_url for those."
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
