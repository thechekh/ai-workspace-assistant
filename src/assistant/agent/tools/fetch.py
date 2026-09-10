"""The `fetch_url` tool — public web pages, with a GitHub API fast path."""

import html
import ipaddress
import re

import httpx2

from assistant.agent.tools.base import Tool
from assistant.rag.repo import GITHUB_RAW, github_headers

# --- fetch_url: public web pages + GitHub repos/accounts ---------------------

_FETCH_MAX_CHARS = 8000
# The body is read in chunks and abandoned past this point: `[:max_chars]` on
# a fully buffered response still meant a multi-gigabyte URL was read into
# memory before being cut down to eight thousand characters.
_FETCH_MAX_BYTES = 1_000_000
_GITHUB_REPO_RE = re.compile(
    r"^https?://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:[/?#].*)?$", re.IGNORECASE
)
_GITHUB_USER_RE = re.compile(r"^https?://github\.com/([\w-]+)/?(?:[?#].*)?$", re.IGNORECASE)
# Integer forms of an address (`2130706433`, `0x7f000001`) that a resolver
# would happily turn into 127.0.0.1.
_NUMERIC_HOST_RE = re.compile(r"^(?:0x[0-9a-f]+|[0-9]+)$", re.IGNORECASE)
_TEXTUAL_TYPES = ("json", "xml", "javascript", "yaml", "markdown", "csv")


def _as_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The IP address a host literal denotes, or None for a name."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if _NUMERIC_HOST_RE.match(host):
        try:
            return ipaddress.IPv4Address(int(host, 0))
        except ValueError:
            return None
    return None


def is_blocked_host(host: str) -> bool:
    """Dev-grade SSRF guard: refuse anything that denotes this machine or a
    private network — loopback, RFC 1918, link-local (cloud metadata lives
    there), the shared CGNAT range, unspecified and IPv4-mapped IPv6 forms.

    Address *literals* are judged by `ipaddress`, which knows every private
    range; the only names refused are `localhost` and its subdomains. A
    public name that resolves to a private address is not caught here —
    production would resolve DNS and enforce an allowlist at the egress proxy.
    """
    host = host.strip("[]").rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    address = _as_address(host)
    if address is None:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
        # 100.64.0.0/10: carrier-grade NAT, treated as internal by every cloud.
        or (isinstance(address, ipaddress.IPv4Address) and address in _CGNAT)
    )


_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class BlockedRedirect(httpx2.HTTPError):
    """A redirect pointed somewhere the initial-URL check would have refused."""


async def _refuse_internal_redirects(response: httpx2.Response) -> None:
    """Re-check the target of every redirect, not just the URL we were given.

    Validating only the first URL is the classic way an SSRF guard gets walked
    past: a perfectly public address answers `302 Location: http://127.0.0.1/…`
    and, with redirects followed, the internal body comes back anyway. This
    runs as a response hook so it covers each hop.
    """
    if not response.has_redirect_location:
        return
    target = response.headers.get("location", "")
    host = httpx2.URL(response.url.join(target)).host or ""
    if is_blocked_host(host):
        raise BlockedRedirect(f"redirect to a private or loopback address ({host}) refused")


def strip_html(page: str) -> str:
    """Crude but dependency-free HTML -> readable text."""
    text = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", page)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _is_textual(content_type: str) -> bool:
    """Whether a response body is worth decoding as text at all.

    A missing content type is given the benefit of the doubt; images,
    archives and binaries are refused before a byte of them is read.
    """
    media = content_type.split(";")[0].strip().lower()
    if not media or media.startswith("text/"):
        return True
    return any(marker in media for marker in _TEXTUAL_TYPES)


async def _github_repo_summary(
    client: httpx2.AsyncClient, owner: str, repo: str, token: str | None
) -> str | None:
    headers = github_headers(token)
    meta = await client.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers)
    if meta.status_code != 200:
        return None
    data = meta.json()
    readme = await client.get(
        f"https://api.github.com/repos/{owner}/{repo}/readme",
        headers={**headers, "Accept": GITHUB_RAW},
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


async def _github_user_summary(
    client: httpx2.AsyncClient, owner: str, token: str | None
) -> str | None:
    headers = github_headers(token)
    user = await client.get(f"https://api.github.com/users/{owner}", headers=headers)
    if user.status_code != 200:
        return None
    data = user.json()
    repos = await client.get(
        f"https://api.github.com/users/{owner}/repos",
        params={"sort": "pushed", "per_page": 15},
        headers=headers,
    )
    lines = (
        [
            f"- {repo['name']} ({repo.get('language') or '-'}): "
            f"{repo.get('description') or '(no description)'}"
            for repo in repos.json()
        ]
        if repos.status_code == 200
        else []
    )
    listing = "\n".join(lines) or "(no public repositories)"
    return (
        f"GitHub account {data.get('login')} ({data.get('type', 'User')})\n"
        f"Name: {data.get('name') or '-'} | Public repos: {data.get('public_repos', 0)}\n\n"
        f"Public repositories (most recently pushed first):\n{listing}"
    )


async def _read_page(http: httpx2.AsyncClient, url: str, max_bytes: int) -> str:
    """GET a page, decoding at most `max_bytes` of it; the rest is never read."""
    async with http.stream("GET", url) as response:
        if response.status_code >= 400:
            return f"error: GET {url} returned HTTP {response.status_code}"
        content_type = response.headers.get("content-type", "")
        if not _is_textual(content_type):
            return f"error: {url} is {content_type.split(';')[0] or 'binary'}, not a text page"
        received = bytearray()
        async for chunk in response.aiter_bytes():
            received.extend(chunk)
            if len(received) >= max_bytes:
                break
        text = bytes(received).decode(response.charset_encoding or "utf-8", errors="replace")
    return strip_html(text) if "html" in content_type else text


def new_http_client() -> httpx2.AsyncClient:
    """The shared outbound client. Created once per app so calls reuse the
    connection pool instead of paying a TCP+TLS handshake each time (the
    GitHub path makes two requests)."""
    return httpx2.AsyncClient(
        timeout=15,
        follow_redirects=True,
        headers={"User-Agent": "ai-workspace-assistant/0.1"},
        # Redirects are followed, so every hop is re-checked against the same
        # host rules as the original URL.
        event_hooks={"response": [_refuse_internal_redirects]},
    )


def make_fetch_url(
    *,
    client: httpx2.AsyncClient | None = None,
    max_chars: int = _FETCH_MAX_CHARS,
    max_bytes: int = _FETCH_MAX_BYTES,
    github_token: str | None = None,
) -> Tool:
    """`client` is the pooled client owned by the app lifespan. Without one a
    short-lived client is created per call — fine for tests and scripts.
    `github_token` authenticates the GitHub fast path, like the repo tools."""

    async def handler(arguments: dict[str, object]) -> str:
        url = str(arguments.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            return "error: only http(s) URLs are supported"
        if is_blocked_host(httpx2.URL(url).host or ""):
            return "error: refusing to fetch private or loopback addresses"

        owned = client is None
        http = client or new_http_client()
        try:
            # GitHub URLs go through the API: clean description + README
            # instead of a megabyte of page chrome.
            if repo_match := _GITHUB_REPO_RE.match(url):
                owner, repo = repo_match.groups()
                summary = await _github_repo_summary(http, owner, repo, github_token)
                if summary:
                    return summary[:max_chars]
            if user_match := _GITHUB_USER_RE.match(url):
                summary = await _github_user_summary(http, user_match.group(1), github_token)
                if summary:
                    return summary[:max_chars]
            text = await _read_page(http, url, max_bytes)
            if text.startswith("error:"):
                return text
            return text.strip()[:max_chars] or "(the page has no extractable text)"
        except httpx2.HTTPError as exc:
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
