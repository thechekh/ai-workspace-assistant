# 06 — Tools & MCP: what the agent can *do*

Full per-tool schemas, examples, and failure modes live in
**[docs/tools.md](../reference/tools.md)** — that's the reference. This chapter is
the working understanding: the inventory, how execution flows, and the
guards.

## The inventory (6 tools, 2 origins)

| Tool | Origin | One line |
|---|---|---|
| `search_docs` | native | RAG over internal docs (chapter 05) — architecture, services, deployment, guidelines, onboarding. Cites `[source — heading]` |
| `fetch_url` | native | Public web pages (HTML→text) and GitHub: repo URL → description+README via API, account URL → public repo list. The "never guess a page's content" tool |
| `code__search_code` | MCP `code` server | Case-insensitive regex over **this** repository (pure Python walker; respects text extensions, skips build dirs) |
| `code__read_file` | MCP `code` server | Read a file from this repository (path-escape guarded, windowed by lines) |
| `github__list_pull_requests` | MCP `github` server (**mock**) | 5 canned PRs, filterable by state |
| `github__get_pull_request` | MCP `github` server (**mock**) | One PR with body + review status |
| `github__list_issues` | MCP `github` server (**mock**) | 3 canned issues |

The GitHub mock exposes the **same tool names** as the official
`ghcr.io/github/github-mcp-server` — swapping in the real one is just the
`ASSISTANT_MCP_SERVERS` JSON + a PAT (see `.env.example`), zero code changes.

## How a tool call executes (the seam)

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
  never repeat a fruitless search.
- **FakeLLM** (offline) uses keyword heuristics — PR words → github tool,
  `search code for X` → code tool, any URL → fetch_url, trailing `?` →
  search_docs — so every tool is demoable with zero keys.

## Adding a tool (two ways)

- **Native**: build a `Tool(name, description, JSON-schema params, async
  handler -> str)` (prefix failures with `error:`), append it in
  `create_app`'s `native_tools`. Telemetry, guards, and all three backends
  come free.
- **MCP**: write `@mcp.tool()` functions on an `MCPServer` (docstring =
  description, type hints = schema — see
  [code_search.py](../../src/assistant/mcp_servers/code_search.py)), add the
  server to `ASSISTANT_MCP_SERVERS`. Or point at any existing MCP server.
