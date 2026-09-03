"""The `ingest_repo` tool — the agent's one write capability.

"Ingest the docs from thechekh/demo-payments-platform" is a chat request, so
the capability lives in the chat path: the agent calls this tool and the very
next question is answered from that repo's documentation. It is deliberately
the *narrowest possible* write — it adds (or refreshes) one repository's
documentation under `owner/repo/path` sources and can touch nothing else: no
deletes, no edits, no other sources. The read-only story becomes "read-only
plus one additive, rate-limited exception", and the allowlist test in
`test_review_regressions.py` pins exactly that.
"""

import logging

import httpx

from assistant.agent.tools.base import Tool
from assistant.config import Settings
from assistant.rag.ingest import ingest_documents
from assistant.rag.repo import RepoIngestError, fetch_repo_documents
from assistant.rag.store import VectorStore

logger = logging.getLogger(__name__)

_DESCRIPTION = (
    "Add a GitHub repository's documentation to the internal knowledge base so "
    "search_docs can answer from it. Fetches every .md/.txt/.rst file in the "
    "repo and indexes them as 'owner/repo/path' sources; running it again for "
    "the same repo refreshes its documents in place. Set include_code=true to "
    "ALSO index the repository's source files (.py/.ts/.go/...), which makes "
    "questions like 'show me the code that handles X' answerable via "
    "search_docs. Public repositories need no credentials. Use when the user "
    "asks to ingest, index, load or learn a repository. This tool only ADDS "
    "documents — it cannot delete or modify anything else. Do not call it "
    "unless the user explicitly asked for a repository to be ingested."
)

_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "repo": {
            "type": "string",
            "description": "owner/repository, e.g. thechekh/demo-payments-platform",
        },
        "ref": {
            "type": "string",
            "description": "branch, tag or commit SHA; the default branch when omitted",
        },
        "include_code": {
            "type": "boolean",
            "description": "also index source code files, not only documentation "
            "(set true when the user wants code searchable)",
        },
    },
    "required": ["repo"],
}


def make_ingest_repo(
    settings: Settings, store: VectorStore, *, client: httpx.AsyncClient | None = None
) -> Tool:
    """`client` is the app's pooled outbound client (a private one is made per
    call without it — fine for tests and scripts)."""

    async def handler(arguments: dict[str, object]) -> str:
        repo = str(arguments.get("repo", "")).strip()
        if not repo:
            return "error: the 'repo' argument is required (owner/repository)"
        ref = str(arguments["ref"]).strip() if arguments.get("ref") else None
        include_code = bool(arguments.get("include_code", False))
        token = settings.github_token.get_secret_value() if settings.github_token else None

        owns_client = client is None
        http = client or httpx.AsyncClient(timeout=30)
        try:
            documents, skipped = await fetch_repo_documents(
                repo, client=http, token=token, ref=ref, include_code=include_code
            )
        except RepoIngestError as exc:
            return f"error: {exc.detail}"
        finally:
            if owns_client:
                await http.aclose()

        if not documents:
            hint = "" if include_code else " (retry with include_code=true to index source files)"
            listing = f" Skipped: {'; '.join(skipped)}" if skipped else ""
            listing = hint + listing
            return f"error: no .md/.txt/.rst files found in {repo!r}.{listing}"

        chunks = await ingest_documents(documents, settings, store=store)
        logger.info("ingest_repo: %d chunks from %d file(s) in %s", chunks, len(documents), repo)
        sources = "\n".join(f"- {source}" for source, _ in documents)
        note = f"\nSkipped: {'; '.join(skipped)}" if skipped else ""
        return (
            f"Indexed {chunks} chunks from {len(documents)} file(s) in {repo}. "
            f"search_docs can now answer from these sources:\n{sources}{note}"
        )

    return Tool(
        name="ingest_repo",
        description=_DESCRIPTION,
        parameters=_PARAMETERS,
        handler=handler,
    )
