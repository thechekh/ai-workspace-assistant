"""Pull a GitHub repository's documentation into the knowledge base.

The upload endpoint covers "I have files"; this covers "the docs live in a
repo". Two listing requests (repo metadata for the default branch, then one
recursive git tree) plus one raw-content request per document — not a crawl.

Sources are namespaced `owner/repo/path`, which is also the fix for a real
collision: flat basenames let a second project's README.md silently replace
the first's (found in live testing). Namespaced sources cannot collide, and
re-ingesting the same repo replaces exactly its own chunks.
"""

import posixpath
import re

import httpx

_API = "https://api.github.com"
# GitHub's own rules, tightened: owner has no dots, repo may have them, and a
# repo named "." / ".." is rejected by the path-segment check below anyway.
REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})?/[A-Za-z0-9._-]{1,100}$")

DOC_SUFFIXES = {".md", ".txt", ".rst"}
# Source code the sparse lexical vector can genuinely match on (identifiers,
# function names). Config/lockfile formats are left out on purpose: a lockfile
# is thousands of lines nobody asks questions about.
CODE_SUFFIXES = {
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".vue",
    ".go",
    ".rs",
    ".rb",
    ".java",
    ".kt",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
    ".php",
    ".sql",
    ".sh",
    ".yml",
    ".yaml",
    ".toml",
}
# Directories that are all bulk and no signal.
SKIP_DIR_PARTS = {
    "node_modules",
    ".git",
    "dist",
    "build",
    "vendor",
    "__pycache__",
    ".venv",
    "venv",
    "target",
    ".next",
    "coverage",
}
_SKIP_FILE_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock", "poetry.lock"}
MAX_FILES = 100  # embedding costs money; a monorepo should not arrive by accident
MAX_FILE_BYTES = 2 * 1024 * 1024  # matches the upload endpoint's cap
MAX_CODE_FILE_BYTES = 300 * 1024  # a source file bigger than this is generated output


class RepoIngestError(Exception):
    """A fetch failure the caller can turn into an actionable HTTP response."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _is_document(path: str, size: int | None, *, include_code: bool) -> str | None:
    """Return a skip reason, or None when the file should be ingested."""
    suffix = posixpath.splitext(path)[1].lower()
    is_doc = suffix in DOC_SUFFIXES
    is_code = include_code and suffix in CODE_SUFFIXES
    if not (is_doc or is_code):
        return "not documentation"
    # The tree is data from an external service: refuse traversal-shaped paths
    # instead of trusting that GitHub would never emit one.
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "unsafe path"
    if is_code and (
        any(part in SKIP_DIR_PARTS for part in parts[:-1])
        or parts[-1] in _SKIP_FILE_NAMES
        or ".min." in parts[-1]
    ):
        return "not documentation"  # bulk, not signal — skip silently
    cap = MAX_FILE_BYTES if is_doc else MAX_CODE_FILE_BYTES
    if size is not None and size > cap:
        return f"larger than {cap // 1024} KB"
    return None


async def fetch_repo_documents(
    repo: str,
    *,
    client: httpx.AsyncClient,
    token: str | None = None,
    ref: str | None = None,
    max_files: int = MAX_FILES,
    include_code: bool = False,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Return ([(source, text)], skipped) for a repository's documentation.

    `include_code` widens the net to source files (CODE_SUFFIXES), so the
    hybrid index can answer "show me the code that ..." — the sparse lexical
    vector matches identifiers exactly.

    `source` is `owner/repo/path` — namespaced so two repositories can never
    overwrite each other's documents.
    """
    if not REPO_RE.match(repo):
        raise RepoIngestError(422, f"{repo!r} is not an owner/repository name")
    headers = _headers(token)

    if ref is None:
        meta = await client.get(f"{_API}/repos/{repo}", headers=headers)
        if meta.status_code == 404:
            raise RepoIngestError(
                404,
                f"repository {repo!r} not found — private repositories need "
                "ASSISTANT_GITHUB_TOKEN with read access to it",
            )
        if meta.status_code in (401, 403):
            raise RepoIngestError(
                meta.status_code,
                "GitHub rejected the token (or the unauthenticated rate limit ran out)",
            )
        meta.raise_for_status()
        ref = str(meta.json()["default_branch"])

    tree = await client.get(
        f"{_API}/repos/{repo}/git/trees/{ref}", params={"recursive": "1"}, headers=headers
    )
    if tree.status_code == 404:
        raise RepoIngestError(404, f"ref {ref!r} not found in {repo!r} (empty repository?)")
    if tree.status_code in (401, 403):
        raise RepoIngestError(
            tree.status_code,
            "GitHub rejected the token (or the unauthenticated rate limit ran out)",
        )
    tree.raise_for_status()
    payload = tree.json()

    documents: list[tuple[str, str]] = []
    skipped: list[str] = []
    if payload.get("truncated"):
        skipped.append("(tree truncated by GitHub — very large repository, listing incomplete)")

    wanted: list[str] = []
    for entry in payload.get("tree", []):
        if entry.get("type") != "blob":
            continue
        path = str(entry.get("path", ""))
        reason = _is_document(path, entry.get("size"), include_code=include_code)
        if reason == "not documentation":
            continue  # silently: most of a repo is code, listing it is noise
        if reason is not None:
            skipped.append(f"{path} ({reason})")
            continue
        if len(wanted) >= max_files:
            skipped.append(f"{path} (over the {max_files}-file limit)")
            continue
        wanted.append(path)

    for path in wanted:
        raw = await client.get(
            f"{_API}/repos/{repo}/contents/{path}",
            params={"ref": ref},
            headers={**headers, "Accept": "application/vnd.github.raw+json"},
        )
        if raw.status_code != 200:
            skipped.append(f"{path} (fetch failed: HTTP {raw.status_code})")
            continue
        documents.append((f"{repo}/{path}", raw.text))

    return documents, skipped


async def fetch_repo_file(
    repo: str,
    path: str,
    *,
    client: httpx.AsyncClient,
    token: str | None = None,
    ref: str | None = None,
) -> str:
    """Fetch one file's text from a GitHub repository (public repos: no token).

    Raises RepoIngestError with an actionable message on any failure — the
    tool layer turns it into an `error:` result the model can react to.
    """
    if not REPO_RE.match(repo):
        raise RepoIngestError(422, f"{repo!r} is not an owner/repository name")
    parts = path.split("/")
    if not path or any(part in ("", ".", "..") for part in parts):
        raise RepoIngestError(422, f"unsafe or empty path: {path!r}")

    params = {"ref": ref} if ref else None
    response = await client.get(
        f"{_API}/repos/{repo}/contents/{path}",
        params=params,
        headers={**_headers(token), "Accept": "application/vnd.github.raw+json"},
    )
    if response.status_code == 404:
        raise RepoIngestError(
            404,
            f"{path!r} not found in {repo!r} — check the path (case matters), or the "
            "repository is private and needs ASSISTANT_GITHUB_TOKEN",
        )
    if response.status_code in (401, 403):
        raise RepoIngestError(
            response.status_code,
            "GitHub rejected the request (bad token, or the unauthenticated rate limit ran out)",
        )
    response.raise_for_status()
    if len(response.content) > MAX_FILE_BYTES:
        raise RepoIngestError(413, f"{path!r} is larger than {MAX_FILE_BYTES // 1024 // 1024} MB")
    return response.text
