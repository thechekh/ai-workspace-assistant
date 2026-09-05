# 06 — MCP (Model Context Protocol)

**What this chapter answers: what problem MCP standardizes, how a tool call
actually travels from the model to a subprocess and back, and what this
project's two bundled servers really do.** It does not cover the generic
(non-MCP) tool-calling contract underneath it — see
[04-tool-calling-and-agents.md](04-tool-calling-and-agents.md) for that; MCP
tools plug into that same contract unchanged.

## 1. The problem: N×M integrations

Every AI assistant needs tools: GitHub, Jira, databases, internal search…
Without a standard, every assistant (N) writes bespoke glue for every system
(M) — N×M integrations, each with its own auth, schema, and bugs. The same
problem existed for editors×languages until the Language Server Protocol,
and for devices×chargers until USB-C.

**MCP** — the Model Context Protocol, an open standard released by Anthropic
in late 2024 and since adopted broadly — is that standardization for AI
tools: build one **MCP server** per system, and *any* MCP-capable client
can use it. N+M instead of N×M.

## 2. The moving parts

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

## 3. The lifecycle, step by step

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
[`mcp/registry.py`](../../src/assistant/mcp/registry.py) is the client side
— **110 lines** (`wc -l src/assistant/mcp/registry.py`, 2026-09-04),
configured via `ASSISTANT_MCP_SERVERS` JSON. Design points to defend:
per-server connect **timeouts**; a server that fails to start is logged and
**skipped** — the agent runs with whatever tools are reachable (graceful
degradation, tested); per-call timeouts; `isError` results become
`error: ...` strings the model can react to.

## 4. Our two bundled servers

| Piece | Role | Credentials | Size |
|---|---|---|---:|
| [`mcp/registry.py`](../../src/assistant/mcp/registry.py) | the client: connects, discovers tools, namespaces them, forwards calls | n/a | **110 lines** |
| [`mcp_servers/code_search.py`](../../src/assistant/mcp_servers/code_search.py) | *real* server: regex `search_code` (pure Python, no ripgrep) plus `read_file` with a path-traversal guard, over this repository | none | **94 lines** |
| [`mcp_servers/fake_github.py`](../../src/assistant/mcp_servers/fake_github.py) | *mock* server: canned `list_pull_requests` / `get_pull_request` / `list_issues`, same tool names as the official GitHub server | none | **131 lines** |

(All three runnable standalone, e.g. `python -m assistant.mcp_servers.code_search`;
sizes measured with `wc -l`, 2026-09-04.)

`fake_github` borrows the official GitHub MCP server's tool names — two of
the three still match upstream; `get_pull_request` has since been renamed
`pull_request_read` there. The mock is a strategy, not a hack: because tools
are *discovered* at startup rather than hardcoded, names need not even
match, and swapping mock → real GitHub is **one config line** (point the
`github` entry at `ghcr.io/github/github-mcp-server` with a PAT) — zero code
changes. The demo runs credential-free today; production is a `.env` edit
tomorrow.

That "one config line" claim has been priced against two real candidates
that didn't ship, both from [future-tools.md](../project/future-tools.md):

| Considered | Verdict | Why |
|---|---|---|
| Atlassian's hosted Jira/Confluence MCP server (`jira_search`) | Rejected by owner | no Atlassian org to demo against — but architecturally it is one more `MCPServerConfig` entry plus a token, the same shape the GitHub server already proves live |
| `workspace-mcp` — serving *this project's own* tools out over streamable HTTP, to editors | Deferred | the mechanism already runs in both directions (our servers are FastMCP; our client already consumes a hosted streamable-HTTP server) — what's missing is a reason to expose the knowledge base over HTTP, not capability |
| The real `ghcr.io/github/github-mcp-server`, live | Deferred (mock stands in) | prototyped and verified reachable; kept mocked so the demo stays credential-free |

## 5. Trust & security (know this cold)

An MCP server is code you run with the permissions you give it — treat
servers like dependencies: run trusted ones, least privilege. Our specifics:
both bundled servers are ours and local; `read_file` is root-jailed;
secrets (like a GitHub PAT) go to the server via environment config, never
through the model's context; and the model still only *requests* calls —
execution stays in our process boundary (chapter 04's rule).

## 6. Questions you might get

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

## 7. Reading it honestly

- **The GitHub integration has never faced a real server.** `fake_github`'s
  canned data means nobody here has seen this codebase handle the official
  server's actual rate limits, auth failures, or schema drift — the mock
  proves the pipeline, not resilience to a live third party.
- **Timeouts, not circuit breakers.** A server that connects fine and then
  hangs on every call pays the full 60-second `_CALL_TIMEOUT_S` on each
  request, every time, until someone disables it — there is no
  "stop trying this server" logic beyond that per-call wait.
- **`code_search` is plain regex, not ranked search.** It returns the first
  `max_results` matches in file-walk order — none of chapter 03's
  hybrid-search-plus-rerank machinery applies here; this is grep with a
  path guard, not retrieval.
- **A tool's description is prompt content the server controls, not
  something the transport enforces.** `search_code`'s own docstring has to
  warn the model in capitals ("NEVER use it for any other repository")
  precisely because nothing below the model layer stops a server from
  describing itself however it likes — trust is a policy decision here, not
  a protocol guarantee.
- **No sandboxing beyond one path-traversal guard.** A local stdio server
  runs with this process's own OS permissions; `read_file`'s root jail
  stops one specific escape, not every possible one.

## 8. Related

- [04-tool-calling-and-agents.md](04-tool-calling-and-agents.md) — the tool contract every MCP tool plugs into unchanged
- [05-agent-frameworks.md](05-agent-frameworks.md) — how the same MCP-adapted tools reach all three agent backends unchanged
- [../handbook/06-tools-mcp.md](../handbook/06-tools-mcp.md) — the full tool inventory and how a call executes here
- [../reference/security.md](../reference/security.md) — the threat model, including exactly what an MCP server is trusted with
- [../project/future-tools.md](../project/future-tools.md) — Jira and `workspace-mcp`, priced and deferred, with their triggers
