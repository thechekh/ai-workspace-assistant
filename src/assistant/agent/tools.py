"""Shared tool registry — every agent backend gets the same tools.

Native tools live here (search_docs over the RAG retriever, fetch_url for
public web pages/GitHub); MCP-provided tools are adapted into this same
registry. `Tool.run` is the single execution seam — span + metrics +
structured log for every call, on every backend.
"""

import html
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
import structlog

from assistant.llm.client import ToolSpec
from assistant.rag.rerank import query_overlap
from assistant.rag.retriever import Retriever
from assistant.telemetry import TOOL_CALLS_TOTAL, TOOL_SECONDS, current_turn_stats, tracer

logger = structlog.get_logger("assistant.tools")

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

    async def run(self, arguments: dict[str, object]) -> str:
        """Execute with telemetry; a crash becomes an error *result*, never an exception."""
        # Duplicate guard: the same call twice in one turn returns a pointer to
        # the earlier result instead of re-executing (models sometimes loop).
        stats = current_turn_stats.get()
        if stats is not None:
            key = (self.name, json.dumps(arguments, sort_keys=True, default=str))
            if key in stats.seen_tool_calls:
                TOOL_CALLS_TOTAL.labels(tool=self.name, status="duplicate").inc()
                logger.warning("tool.duplicate_call", tool=self.name)
                return (
                    f"error: duplicate call — {self.name} already ran with exactly these "
                    "arguments in this turn. Use the result you already received above; "
                    "do not repeat the call."
                )
            stats.seen_tool_calls.add(key)

        start = time.perf_counter()
        status = "ok"
        with tracer.start_as_current_span("tool.execute") as span:
            span.set_attribute("tool.name", self.name)
            try:
                result = await self.handler(dict(arguments))
            except Exception as exc:  # a tool crash must not kill the agent loop
                status = "crash"
                result = f"error: tool {self.name!r} failed: {exc}"
                logger.warning("tool.crashed", tool=self.name, exc_info=True)
            if status == "ok" and result.startswith("error:"):
                status = "error"
            span.set_attribute("tool.status", status)
            span.set_attribute("tool.result_chars", len(result))
        duration = time.perf_counter() - start
        TOOL_SECONDS.labels(tool=self.name).observe(duration)
        TOOL_CALLS_TOTAL.labels(tool=self.name, status=status).inc()
        logger.info(
            "tool.executed",
            tool=self.name,
            status=status,
            duration_ms=round(duration * 1000),
            result_chars=len(result),
        )
        return result


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
            TOOL_CALLS_TOTAL.labels(tool=name, status="unknown").inc()
            logger.warning("tool.unknown", tool=name)
            return f"error: unknown tool {name!r}"
        return await tool.run(arguments)


_NO_RELEVANT_DOCS = (
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
        results = await retriever.search(query, limit=limit)
        # Relevance gate: retrieval always returns top-k, but RRF/hash scores
        # are not calibrated — drop chunks sharing no meaningful token with
        # the query instead of handing the model confident-looking noise.
        results = [result for result in results if query_overlap(query, result.text) > 0]
        if not results:
            return _NO_RELEVANT_DOCS
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


def make_fetch_url(*, max_chars: int = _FETCH_MAX_CHARS) -> Tool:
    async def handler(arguments: dict[str, object]) -> str:
        url = str(arguments.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            return "error: only http(s) URLs are supported"
        host = httpx.URL(url).host or ""
        if _BLOCKED_HOST_RE.match(host):
            return "error: refusing to fetch private or loopback addresses"
        try:
            async with httpx.AsyncClient(
                timeout=15,
                follow_redirects=True,
                headers={"User-Agent": "ai-workspace-assistant/0.1"},
            ) as client:
                # GitHub URLs go through the API: clean description + README
                # instead of a megabyte of page chrome.
                if repo_match := _GITHUB_REPO_RE.match(url):
                    summary = await _github_repo_summary(client, *repo_match.groups())
                    if summary:
                        return summary[:max_chars]
                if user_match := _GITHUB_USER_RE.match(url):
                    summary = await _github_user_summary(client, user_match.group(1))
                    if summary:
                        return summary[:max_chars]
                response = await client.get(url)
                if response.status_code >= 400:
                    return f"error: GET {url} returned HTTP {response.status_code}"
                content_type = response.headers.get("content-type", "")
                text = strip_html(response.text) if "html" in content_type else response.text
                return text.strip()[:max_chars] or "(the page has no extractable text)"
        except httpx.HTTPError as exc:
            return f"error: could not fetch {url}: {exc}"

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
