"""The `repo_read_file` tool — one exact file from any GitHub repository.

Retrieval finds the right chunk; this fetches the surrounding file. The pair
("search names a source, read opens it") is what lets the assistant show real
code instead of paraphrasing an excerpt — and for public repositories it
needs no credentials at all, so the project's value never hinges on a PAT.

Read-only by construction: one GET against api.github.com with a validated
owner/repo and path. The model never supplies a URL.
"""

import logging

import httpx

from assistant.agent.tools.base import Tool
from assistant.config import Settings
from assistant.rag.repo import RepoIngestError, fetch_repo_file

logger = logging.getLogger(__name__)

_DESCRIPTION = (
    "Read ONE file from a GitHub repository by exact path — public repositories "
    "need no token. Use it after search_docs surfaces an ingested code chunk "
    "(its source is 'owner/repo/path' — pass the repo and path parts here), or "
    "when the user names a specific file. Returns the file's text; long files "
    "are truncated with a marker. Read-only: it cannot change anything."
)

_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "repo": {
            "type": "string",
            "description": "owner/repository, e.g. thechekh/savesong",
        },
        "path": {
            "type": "string",
            "description": "file path inside the repository, e.g. src/matching/scorer.py",
        },
        "ref": {
            "type": "string",
            "description": "branch, tag or commit SHA; the default branch when omitted",
        },
    },
    "required": ["repo", "path"],
}


def make_repo_read_file(settings: Settings, *, client: httpx.AsyncClient | None = None) -> Tool:
    """`client` is the app's pooled outbound client (a private one is made per
    call without it — fine for tests and scripts)."""

    async def handler(arguments: dict[str, object]) -> str:
        repo = str(arguments.get("repo", "")).strip()
        path = str(arguments.get("path", "")).strip().lstrip("/")
        if not repo or not path:
            return "error: both 'repo' (owner/repository) and 'path' are required"
        ref = str(arguments["ref"]).strip() if arguments.get("ref") else None
        token = settings.github_token.get_secret_value() if settings.github_token else None

        owns_client = client is None
        http = client or httpx.AsyncClient(timeout=30)
        try:
            text = await fetch_repo_file(repo, path, client=http, token=token, ref=ref)
        except RepoIngestError as exc:
            return f"error: {exc.detail}"
        finally:
            if owns_client:
                await http.aclose()

        logger.info("repo_read_file: %s/%s (%d chars)", repo, path, len(text))
        return f"// {repo}/{path}\n{text}"

    return Tool(
        name="repo_read_file",
        description=_DESCRIPTION,
        parameters=_PARAMETERS,
        handler=handler,
    )
