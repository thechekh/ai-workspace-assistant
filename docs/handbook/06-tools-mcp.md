# 06 — Tools & MCP: what the agent can *do*

Full per-tool schemas, examples, and failure modes live in
**[the tool reference](../reference/tools.md)** — that's the reference. This chapter is
the working understanding: the inventory, how execution flows, and the
guards.

## The inventory (9 tools, 2 origins)

| Tool | Origin | One line |
|---|---|---|
| `search_docs` | native | Hybrid RAG over the knowledge base (chapter 05): uploaded docs **and every ingested repo — documentation and code**. Cites `[source — heading]`; on a code hit it hands the model the exact `repo_read_file` call |
| `repo_read_file` | native | One exact file from any GitHub repo — tokenless for public repos; the "show me the real code" tool |
| `ingest_repo` | native | **The one write tool**: "ingest owner/name" pulls a repo's docs (and with `include_code`, its source files) into the KB as `owner/repo/path` sources. Additive only, explicit-ask only — both pinned by tests |
| `fetch_url` | native | Public web pages (HTML→text) and GitHub: repo URL → description+README via API, account URL → public repo list. The "never guess a page's content" tool |
| `code__search_code` | MCP `code` server | Case-insensitive regex over **this** repository (pure Python walker; respects text extensions, skips build dirs) |
| `code__read_file` | MCP `code` server | Read a file from this repository (path-escape guarded, windowed by lines) |
| `github__list_pull_requests` | MCP `github` server (**mock**) | 5 canned PRs, filterable by state |
| `github__get_pull_request` | MCP `github` server (**mock**) | One PR with body + review status |
| `github__list_issues` | MCP `github` server (**mock**) | 3 canned issues |

### Swapping in the real GitHub server

The mock borrows the official server's names, and the swap really is just the
`ASSISTANT_MCP_SERVERS` JSON plus a PAT (see `.env.example`) — **zero code
changes**. Verified by pointing this project's own `MCPRegistry` at
`ghcr.io/github/github-mcp-server`: it connected and discovered its tools,
correctly namespaced as `github__*`.

Two things that measurement turned up, both worth knowing before you swap:

- **One name has drifted.** `list_pull_requests` and `list_issues` still
  match, but the official server renamed `get_pull_request` to
  `pull_request_read`. Nothing breaks — tools are *discovered* at startup, not
  hardcoded — but the mock is no longer a name-for-name stand-in.
- **It exposes 44 tools, against the mock's 3.** Their JSON schemas are
  ~12,900 tokens, versus ~260 for the mock, and that rides in *every* prompt
  before the user has asked anything. On a small model that measurably hurts
  tool selection; on a free tier it alone can exceed a 12k
  tokens-per-minute allowance for `gpt-4.1-nano`. Use the server's
  toolset flags (`--toolsets pull_requests,issues`) to trim it if you swap.

That cost is the real argument for the mock in *development*, and it is a
better answer than "we didn't have a token": the mock proves the
*architecture* — discovery, namespacing, graceful degradation — at 3 tools
instead of 44, for free, in CI, with no credentials.

It is not the answer for a demo. The mock is the development profile; the
production profile runs the real server. See
[demo-runbook.md](../project/demo-runbook.md) and
[`.env.production.example`](../../.env.production.example).

### Two ways to connect the real one

| | Hosted (`transport: http`) | Container (`transport: stdio`) |
|---|---|---|
| Endpoint | `https://api.githubcopilot.com/mcp/` | `ghcr.io/github/github-mcp-server` |
| Needs Docker | no | yes |
| Auth | `Authorization: Bearer <PAT>` header | `GITHUB_PERSONAL_ACCESS_TOKEN` env |
| Trim toolsets | `X-MCP-Toolsets` header, `/readonly` path | `GITHUB_TOOLSETS` env |

The hosted route needs authentication headers, which is why `MCPServerConfig`
carries a **`headers`** field ([config.py](../../src/assistant/config.py)):
without it the `http` transport can only reach unauthenticated servers, so the
hosted GitHub server was unreachable regardless of the PAT. The headers become
an `httpx` client handed to the transport in
[`MCPRegistry._connect`](../../src/assistant/mcp/registry.py), and
`tests/test_mcp.py` asserts the credential actually reaches the wire.

## How a tool call executes (the seam)

Four guards live on this one seam, so every tool — native or MCP, any
backend — gets them for free: crash isolation (a tool exception becomes an
`error:` *result*), the duplicate-call guard, telemetry (span + metrics +
log), and a **20k-char cap on the result** before it re-enters the prompt —
tool output is billed prompt tokens, and an uncapped listing once cost 57x a
normal turn.

Every call — from any of the three backends — funnels through **`Tool.run`**
([agent/tools/base.py](../../src/assistant/agent/tools/base.py)):

```
model emits tool_call
  └─ registry.execute(name, args)        unknown name -> "error: unknown tool"
       └─ Tool.run(args)
            1. duplicate guard      same (tool,args) this turn? -> pointer msg
            2. span tool.execute    + timing histogram
            3. handler(args)        crash -> "error: tool X failed: ..." RESULT
            4. metrics + log        tool_calls_total{tool,status}, tool.executed
       └─ result string appended to the conversation -> next LLM step
```

Three properties to remember:

- **A tool can never kill a turn.** Exceptions become error *results* the
  model can react to (`status="crash"` in metrics). Qdrant down → the agent
  apologizes about docs but the socket keeps serving.
- **Duplicate-call guard** — models (llama especially) sometimes repeat the
  exact same call in one turn; we measured 3× the same `fetch_url`, 31 s and
  15k tokens. A per-turn `(tool, canonical-args)` set (carried on the turn's
  `TurnStats` ContextVar) answers repeats with *"you already ran this — use
  the earlier result"* (`status="duplicate"`). Fresh turn → fresh set.
- **Statuses**: `ok` | `error` (handler said `error:`) | `crash` (raised) |
  `duplicate` | `unknown` — all visible in
  `assistant_tool_calls_total{tool,status}` and the `tool.executed` log.

## How MCP wiring works

At startup, [`MCPRegistry`](../../src/assistant/mcp/registry.py):

1. reads `ASSISTANT_MCP_SERVERS` (default: the two bundled servers, spawned
   as stdio subprocesses; `{python}` resolves to the current interpreter so
   any venv works);
2. connects each (15 s timeout), calls `list_tools`, and wraps every remote
   tool as a registry `Tool` named `<server>__<tool>` — backends can't tell
   MCP tools from native ones (60 s per-call timeout);
3. an unreachable server is logged and **skipped** — the agent runs with
   whatever connected (`/api/health` → `mcp.tools` shows what's live).

Transport can also be `http` (streamable-HTTP URL) for remote servers.

## Who decides to call a tool?

- A **real model** picks from the tool descriptions (that's why descriptions
  are written as instructions: *"Search the INTERNAL engineering
  documentation only … use fetch_url for external URLs"*), steered by the
  system prompt ([config.py](../../src/assistant/config.py)): search_docs first
  for internal questions, fetch_url for URLs, never invent page content,
  retry a fruitless search with *different* terms then report what was
  searched — never claim something does not exist, and never claim a search
  that was not made this turn.
- **FakeLLM** (offline) uses keyword heuristics — PR words → github tool,
  `search code for X` → code tool, any URL → fetch_url, trailing `?` →
  search_docs — so every tool is demoable with zero keys.

## Adding a tool (two ways)

- **Native**: build a `Tool(name, description, JSON-schema params, async
  handler -> str)` (prefix failures with `error:`), append it in
  `native_tools` in `build_runtime()`. Telemetry, guards, and all three backends
  come free.
- **MCP**: write `@mcp.tool()` functions on a `FastMCP` server (docstring =
  description, type hints = schema — see
  [code_search.py](../../src/assistant/mcp_servers/code_search.py)), add the
  server to `ASSISTANT_MCP_SERVERS`. Or point at any existing MCP server.
