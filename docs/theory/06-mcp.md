# 06 — MCP (Model Context Protocol)

## The problem: N×M integrations

Every AI assistant needs tools: GitHub, Jira, databases, internal search…
Without a standard, every assistant (N) writes bespoke glue for every system
(M) — N×M integrations, each with its own auth, schema, and bugs. The same
problem existed for editors×languages until the Language Server Protocol,
and for devices×chargers until USB-C.

**MCP** — the Model Context Protocol, an open standard released by Anthropic
in late 2024 and since adopted broadly — is that standardization for AI
tools: build one **MCP server** per system, and *any* MCP-capable client
can use it. N+M instead of N×M.

## The moving parts

- **MCP server** — a small program that *exposes capabilities* over the
  protocol. The primitives are **tools** (functions the model may call —
  what we use), plus resources (readable data) and prompts (templates).
- **MCP client** — lives inside the AI application; connects to servers,
  discovers their tools, and forwards calls.
- **Transport** — how bytes move (JSON-RPC 2.0 messages either way):
  - **stdio**: the client *spawns the server as a subprocess* and talks over
    stdin/stdout. Perfect for local tools; zero network setup.
  - **streamable HTTP**: the server is a remote HTTP endpoint. For shared/
    hosted servers.

## The lifecycle, step by step

What actually happens when our app starts:

1. For each configured server, the registry **spawns** it (stdio) or
   connects (HTTP).
2. **`initialize`** handshake — versions and capabilities.
3. **`list_tools`** — the server describes its tools: name, description,
   JSON schema. *Same contract as chapter 04* — that's the elegance: MCP
   tools plug straight into the ordinary tool-calling machinery.
4. Each discovered tool is wrapped and **namespaced** —
   `search_code` from server `code` becomes `code__search_code` (no
   collisions between servers) — and dropped into the same `ToolRegistry`
   the agent loop already uses.
5. During chat, a model's call to `code__search_code` is forwarded as
   **`call_tool`** to the subprocess; the text result flows back as an
   ordinary tool result.

**In this project:**
[`mcp/registry.py`](../../src/assistant/mcp/registry.py) (the client side,
~100 lines), configured via `ASSISTANT_MCP_SERVERS` JSON. Design points to
defend: per-server connect **timeouts**; a server that fails to start is
logged and **skipped** — the agent runs with whatever tools are reachable
(graceful degradation, tested); per-call timeouts; `isError` results become
`error: ...` strings the model can react to.

## Our two bundled servers

([`mcp_servers/`](../../src/assistant/mcp_servers/) — each ~100 lines, runnable
standalone: `python -m assistant.mcp_servers.code_search`)

- **`code_search`** — a *real* server, zero credentials: regex `search_code`
  over this repository (pure Python — no ripgrep dependency) and `read_file`
  with a **path-traversal guard** (resolves the path and rejects anything
  escaping the repo root — the security question, pre-answered).
- **`fake_github`** — a *mock* with realistic canned PRs/issues that
  deliberately uses the official GitHub MCP server's tool names
  (`list_pull_requests`, `get_pull_request`, `list_issues`).

The mock is a strategy, not a hack: because the tool names match, swapping
mock → real GitHub is **one config line** (point the `github` entry at
`ghcr.io/github/github-mcp-server` with a PAT) — zero code changes. The
demo runs credential-free today; production is a `.env` edit tomorrow.

## Trust & security (know this cold)

An MCP server is code you run with the permissions you give it — treat
servers like dependencies: run trusted ones, least privilege. Our specifics:
both bundled servers are ours and local; `read_file` is root-jailed;
secrets (like a GitHub PAT) go to the server via environment config, never
through the model's context; and the model still only *requests* calls —
execution stays in our process boundary (chapter 04's rule).

## Questions you might get

**"Why MCP instead of just writing the tools as Python functions?"** — For
in-process tools we do exactly that (`search_docs`). MCP buys three things:
reuse of *existing* servers (GitHub's official one — we didn't write our
GitHub integration and never will), language/process isolation (a server can
be Go, Node, anything), and portability (our `code_search` server would work
unchanged in Claude Desktop, Cursor, or any MCP client).

**"What happens if a server dies mid-session?"** — Its calls fail; the
registry's timeout turns that into an `error:` tool result the model sees
and works around. At startup, unreachable servers are skipped with a warning
— tested behavior, not a hope.

**"stdio vs HTTP — when which?"** — stdio for local, per-instance tools
(spawned and owned by the app — our default). HTTP for shared/remote
servers with their own lifecycle and auth.

**"Isn't the GitHub demo fake?"** — The *data* is canned; the entire
pipeline is real: subprocess spawn, JSON-RPC handshake, tool discovery,
namespacing, call forwarding, result rendering. The swap to real data is
config, and saying that plainly — with the swap line on the slide — is the
strongest form of the demo.
