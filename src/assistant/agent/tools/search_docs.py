"""The `search_docs` tool — RAG over the knowledge base.

The knowledge base starts **empty**: documents are added at runtime through
`POST /api/documents` (or the ingest CLI). So the tool distinguishes three
outcomes, because they need different answers from the model:

- nothing indexed yet   -> tell the user to add documents
- indexed but no match  -> live inventory + filename hits + a retry contract
- matches               -> the chunks, with source and heading for citation
"""

import re

from assistant.agent.tools.base import Tool
from assistant.rag.retriever import Retriever

_TOKEN_RE = re.compile(r"\w+")

_MAX_CHUNK_CHARS = 1500

NOTHING_INDEXED = (
    "The knowledge base is empty — no documents have been added yet. Tell the "
    "user to upload documents (the Documents panel in the UI, or "
    "POST /api/documents) before asking about internal material. Do not retry "
    "this tool."
)

# Kept as the stable first line of the zero-result reply (tests match on it);
# the handler appends a dynamic inventory + retry instructions, because the
# original static text ("do not retry with a rephrased query") taught the
# model to surrender after one literal miss — observed live: one query,
# then a confident "this code does not exist" about code that existed.
NO_RELEVANT_DOCS = "No relevant chunks matched this exact wording."


def _zero_result_help(query: str, sources: list[tuple[str, int]]) -> str:
    """What the model needs at the moment a search comes back empty.

    A dead-end result produces a dead-end answer. This returns the live
    inventory (so "that repo is not ingested" is knowable, not guessed), any
    indexed *filenames* sharing a token with the query (a concept is often
    named in a path even when its word is absent from text), and the retry
    contract: different terms, limited attempts, then an honest report of
    what was searched.
    """
    by_repo: dict[str, int] = {}
    for source, _count in sources:
        parts = source.split("/")
        repo = "/".join(parts[:2]) if len(parts) >= 3 else "(uploaded files)"
        by_repo[repo] = by_repo.get(repo, 0) + 1
    inventory = "; ".join(f"{repo} ({count} files)" for repo, count in sorted(by_repo.items()))

    tokens = {token.lower() for token in _TOKEN_RE.findall(query) if len(token) >= 3}
    matches = [
        source
        for source, _count in sources
        if any(token in source.rsplit("/", 1)[-1].lower() for token in tokens)
    ][:10]
    named = (
        ("\nIndexed files whose NAME matches the query: " + ", ".join(matches)) if matches else ""
    )

    return (
        f"{NO_RELEVANT_DOCS}\nIndexed right now: {inventory}.{named}\n"
        "Retry up to two times with DIFFERENT terms — synonyms, likely "
        "identifier names (camelCase/snake_case), component or file names — "
        "before concluding anything. Never claim something does not exist; "
        "report which terms you searched. If the user's question is about a "
        "repository not listed above, say it is not ingested and that "
        "ingest_repo can add it."
    )


_CODE_SUFFIXES = (
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".vue", ".go", ".rs",
    ".rb", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cs", ".php",
    ".sql", ".sh",
)  # fmt: skip


def _code_hint(results: list) -> str:
    """The chained call, spelled out, for the best code hit (if any)."""
    for result in results:
        source = result.source
        if not source.endswith(_CODE_SUFFIXES):
            continue
        parts = source.split("/")
        if len(parts) < 3:
            continue  # not an owner/repo/path source
        repo, path = "/".join(parts[:2]), "/".join(parts[2:])
        return (
            "\n\n---\n\nThese excerpts include source code. If the user asked to "
            "see the code, call repo_read_file NOW with "
            f'repo="{repo}" and path="{path}" and quote the relevant lines - '
            "do not ask for permission first."
        )
    return ""


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
            return _zero_result_help(query, await retriever.store.list_sources())

        blocks: list[str] = []
        for result in results:
            text = result.text
            if len(text) > _MAX_CHUNK_CHARS:
                text = text[:_MAX_CHUNK_CHARS] + "…"
            blocks.append(
                f"[{result.source} — {result.heading}] (score {result.score:.2f})\n{text}"
            )
        listing = "\n\n---\n\n".join(blocks)
        # A small model follows tool results far more reliably than the system
        # prompt: when the top hits include source code from an ingested repo,
        # hand it the exact next call instead of hoping it infers one — without
        # this it found the right file and then *asked permission* to open it.
        hint = _code_hint(results)
        return f"{listing}{hint}" if hint else listing

    return Tool(
        name="search_docs",
        description=(
            "Search the knowledge base: documents the team has added to this "
            "assistant (architecture, services, deployment, guidelines, "
            "onboarding) AND every ingested GitHub repository — their "
            "documentation and their source code alike, with sources named "
            "owner/repo/path. Call this whenever the user asks about our "
            "systems or about any repository that has been ingested, "
            "including 'show me the code that ...' questions — then open the "
            "full file with repo_read_file. Only for websites and repos that "
            "were never ingested use fetch_url instead."
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
