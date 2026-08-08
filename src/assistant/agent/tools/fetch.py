"""The `fetch_url` tool — public web pages, with a GitHub API fast path."""

import html
import re

import httpx

from assistant.agent.tools.base import Tool

# --- fetch_url: public web pages + GitHub repos/accounts ---------------------

_FETCH_MAX_CHARS = 8000
_GITHUB_REPO_RE = re.compile(
    r"^https?://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:[/?#].*)?$", re.IGNORECASE
)
_GITHUB_USER_RE = re.compile(r"^https?://github\.com/([\w-]+)/?(?:[?#].*)?$", re.IGNORECASE)
# Dev-grade SSRF guard: refuse obvious loopback/private/link-local hosts.
# (Production would resolve DNS and enforce an allowlist at the egress proxy.)
_BLOCKED_HOST_RE = re.compile(
    r"^(localhost$|127\.|0\.|10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.)", re.IGNORECASE
)
_GITHUB_JSON = {"Accept": "application/vnd.github+json"}


def strip_html(page: str) -> str:
    """Crude but dependency-free HTML -> readable text."""
    text = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", page)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


async def _github_repo_summary(client: httpx.AsyncClient, owner: str, repo: str) -> str | None:
    meta = await client.get(f"https://api.github.com/repos/{owner}/{repo}", headers=_GITHUB_JSON)
    if meta.status_code != 200:
        return None
    data = meta.json()
    readme = await client.get(
        f"https://api.github.com/repos/{owner}/{repo}/readme",
        headers={"Accept": "application/vnd.github.raw+json"},
    )
    readme_text = readme.text.strip()[:6000] if readme.status_code == 200 else "(no README)"
    topics = ", ".join(data.get("topics") or []) or "-"
    return (
        f"GitHub repository {data.get('full_name')}\n"
        f"Description: {data.get('description') or '(none)'}\n"
        f"Language: {data.get('language') or '-'} | Stars: {data.get('stargazers_count', 0)} "
        f"| Topics: {topics} | Updated: {str(data.get('pushed_at', ''))[:10]}\n\n"
        f"README:\n{readme_text}"
    )


async def _github_user_summary(client: httpx.AsyncClient, owner: str) -> str | None:
    user = await client.get(f"https://api.github.com/users/{owner}", headers=_GITHUB_JSON)
    if user.status_code != 200:
        return None
    data = user.json()
    repos = await client.get(
        f"https://api.github.com/users/{owner}/repos",
        params={"sort": "pushed", "per_page": 15},
        headers=_GITHUB_JSON,
    )
    lines = []
    if repos.status_code == 200:
        for repo in repos.json():
            lines.append(
                f"- {repo['name']} ({repo.get('language') or '-'}): "
                f"{repo.get('description') or '(no description)'}"
            )
    listing = "\n".join(lines) or "(no public repositories)"
    return (
        f"GitHub account {data.get('login')} ({data.get('type', 'User')})\n"
        f"Name: {data.get('name') or '-'} | Public repos: {data.get('public_repos', 0)}\n\n"
        f"Public repositories (most recently pushed first):\n{listing}"
    )


def new_http_client() -> httpx.AsyncClient:
    """The shared outbound client. Created once per app so calls reuse the
    connection pool instead of paying a TCP+TLS handshake each time (the
    GitHub path makes two requests)."""
    return httpx.AsyncClient(
        timeout=15,
        follow_redirects=True,
        headers={"User-Agent": "ai-workspace-assistant/0.1"},
    )


def make_fetch_url(
    *, client: httpx.AsyncClient | None = None, max_chars: int = _FETCH_MAX_CHARS
) -> Tool:
    """`client` is the pooled client owned by the app lifespan. Without one a
    short-lived client is created per call — fine for tests and scripts."""

    async def handler(arguments: dict[str, object]) -> str:
        url = str(arguments.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            return "error: only http(s) URLs are supported"
        host = httpx.URL(url).host or ""
        if _BLOCKED_HOST_RE.match(host):
            return "error: refusing to fetch private or loopback addresses"

        owned = client is None
        http = client or new_http_client()
        try:
            # GitHub URLs go through the API: clean description + README
            # instead of a megabyte of page chrome.
            if repo_match := _GITHUB_REPO_RE.match(url):
                summary = await _github_repo_summary(http, *repo_match.groups())
                if summary:
                    return summary[:max_chars]
            if user_match := _GITHUB_USER_RE.match(url):
                summary = await _github_user_summary(http, user_match.group(1))
                if summary:
                    return summary[:max_chars]
            response = await http.get(url)
            if response.status_code >= 400:
                return f"error: GET {url} returned HTTP {response.status_code}"
            content_type = response.headers.get("content-type", "")
            text = strip_html(response.text) if "html" in content_type else response.text
            return text.strip()[:max_chars] or "(the page has no extractable text)"
        except httpx.HTTPError as exc:
            return f"error: could not fetch {url}: {exc}"
        finally:
            if owned:
                await http.aclose()

    return Tool(
        name="fetch_url",
        description=(
            "Fetch a public web page and return its readable text. For GitHub "
            "URLs it returns clean metadata: a repository's description and "
            "README, or an account's list of public repositories. Use this "
            "whenever the user asks about a URL, an external repository, or a "
            "project on the web — never guess what a page contains."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute http(s) URL to fetch"}
            },
            "required": ["url"],
        },
        handler=handler,
    )
