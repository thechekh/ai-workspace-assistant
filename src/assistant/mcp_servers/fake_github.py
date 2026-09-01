"""A *mocked* GitHub MCP server — canned data, zero credentials.

Borrows tool names from the official GitHub MCP server (two of the three
still match upstream; `get_pull_request` has since been renamed
`pull_request_read` there)
(ghcr.io/github/github-mcp-server), so swapping the mock for the real thing
later is purely an ASSISTANT_MCP_SERVERS config change — no code changes.

Run standalone over stdio:
    python -m assistant.mcp_servers.fake_github
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("github-mock")

_PULL_REQUESTS = [
    {
        "number": 142,
        "title": "feat(agent): LangGraph backend behind the config switch",
        "author": "o.kovalenko",
        "branch": "feat/agent-langgraph",
        "state": "open",
        "checks": "CI passing",
        "reviews": "1 approval, 1 review pending",
        "updated": "2026-08-04",
        "body": "Adds the LangGraph agent backend implementing the shared AgentBackend "
        "protocol. Checkpointing is in-memory for now; Redis saver tracked in #135.",
    },
    {
        "number": 141,
        "title": "fix(rag): keep code fences intact when chunking",
        "author": "d.chekh",
        "branch": "fix/chunking-code-fences",
        "state": "open",
        "checks": "CI passing",
        "reviews": "changes requested",
        "updated": "2026-08-03",
        "body": "Blank lines inside fenced code blocks were splitting paragraphs. "
        "Fence-aware paragraph splitting + regression test.",
    },
    {
        "number": 140,
        "title": "feat(frontend): tool-call cards in the chat UI",
        "author": "m.sydorenko",
        "branch": "feat/tool-cards",
        "state": "merged",
        "checks": "CI passing",
        "reviews": "2 approvals",
        "updated": "2026-08-01",
        "body": "Renders tool_call / tool_result WS events as cards with args and results.",
    },
    {
        "number": 139,
        "title": "chore: bump qdrant-client to 1.19",
        "author": "renovate[bot]",
        "state": "merged",
        "branch": "chore/qdrant-1.19",
        "checks": "CI passing",
        "reviews": "1 approval",
        "updated": "2026-07-30",
        "body": "Routine dependency bump; no API changes affecting us.",
    },
    {
        "number": 138,
        "title": "fix(ws): send an error frame when Redis is down",
        "author": "d.chekh",
        "branch": "fix/ws-redis-error-frame",
        "state": "open",
        "checks": "CI failing (flaky websocket test)",
        "reviews": "draft",
        "updated": "2026-07-29",
        "body": "Infra failures killed the socket silently. Now the client gets an "
        "error frame and the connection keeps serving.",
    },
]

_ISSUES = [
    {"number": 135, "title": "Redis checkpoint saver for LangGraph backend", "state": "open"},
    {
        "number": 129,
        "title": "Embedding comparison: voyage-3 vs text-embedding-3-small",
        "state": "open",
    },
    {"number": 118, "title": "Flaky websocket reconnect test on Windows CI", "state": "open"},
]


@mcp.tool()
def list_pull_requests(state: str = "open", limit: int = 5) -> str:
    """List pull requests in the repository, newest first (mocked data)."""
    wanted = state.lower()
    rows = [pr for pr in _PULL_REQUESTS if wanted in {"all", "any"} or pr["state"] == wanted][
        :limit
    ]
    if not rows:
        return f"no pull requests with state {state!r}"
    return "\n".join(
        f"#{pr['number']} [{pr['state']}] {pr['title']} — by {pr['author']}, "
        f"{pr['checks']}, {pr['reviews']}, updated {pr['updated']}"
        for pr in rows
    )


@mcp.tool()
def get_pull_request(number: int) -> str:
    """Get one pull request with its description and review status (mocked data)."""
    for pr in _PULL_REQUESTS:
        if pr["number"] == number:
            return (
                f"#{pr['number']} {pr['title']}\n"
                f"state: {pr['state']} | branch: {pr['branch']} | author: {pr['author']}\n"
                f"checks: {pr['checks']} | reviews: {pr['reviews']} | updated: {pr['updated']}\n\n"
                f"{pr['body']}"
            )
    return f"error: pull request #{number} not found"


@mcp.tool()
def list_issues(state: str = "open", limit: int = 10) -> str:
    """List issues in the repository (mocked data)."""
    rows = [issue for issue in _ISSUES if state in {"all", "any"} or issue["state"] == state][
        :limit
    ]
    if not rows:
        return f"no issues with state {state!r}"
    return "\n".join(f"#{issue['number']} [{issue['state']}] {issue['title']}" for issue in rows)


if __name__ == "__main__":
    mcp.run()  # stdio transport
