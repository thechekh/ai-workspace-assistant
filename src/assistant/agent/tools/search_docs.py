"""The `search_docs` tool — RAG over the knowledge base.

The knowledge base starts **empty**: documents are added at runtime through
`POST /api/documents` (or the ingest CLI). So the tool distinguishes three
outcomes, because they need different answers from the model:

- nothing indexed yet   -> tell the user to add documents
- indexed but no match  -> tell the user the docs don't cover it, don't retry
- matches               -> the chunks, with source and heading for citation
"""

from assistant.agent.tools.base import Tool
from assistant.rag.retriever import Retriever

_MAX_CHUNK_CHARS = 1500

NOTHING_INDEXED = (
    "The knowledge base is empty — no documents have been added yet. Tell the "
    "user to upload documents (the Documents panel in the UI, or "
    "POST /api/documents) before asking about internal material. Do not retry "
    "this tool."
)

NO_RELEVANT_DOCS = (
    "No relevant documents found in the knowledge base. Do not retry with a "
    "rephrased query — tell the user their indexed documents do not cover "
    "this topic."
)


def make_search_docs(retriever: Retriever, *, limit: int = 4) -> Tool:
    async def handler(arguments: dict[str, object]) -> str:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return "error: the 'query' argument is required"

        # An empty index is a different problem from an unlucky query, and
        # only one of them is the user's to fix.
        if not await retriever.store.exists():
            return NOTHING_INDEXED

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
            "Search the knowledge base: documents the team has added to this "
            "assistant (architecture, services, deployment, guidelines, "
            "onboarding, or whatever else was uploaded). Call this whenever "
            "the user asks about our systems, services, or processes. It "
            "knows nothing about external websites, GitHub repositories, or "
            "other companies — use fetch_url for those."
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
