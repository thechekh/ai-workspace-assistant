# Agent tools — the complete reference

Every tool the agent can call: what it does, its exact parameters, how it is
implemented, and what you see in logs/traces/metrics when it runs. All three
agent backends (custom / Pydantic AI / LangGraph) share the same tools through
one registry — a tool is written once and works everywhere.

## How tools work (the plumbing)

**One shape.** A tool is a [`Tool`](../../src/assistant/agent/tools/base.py) dataclass:
`name`, `description`, `parameters` (a JSON Schema), and an async `handler`
that takes a `dict` of arguments and returns a `str`. `Tool.spec` converts it
to the OpenAI function-calling wire format that every LLM provider we use
understands (OpenAI, Ollama, Gemini — and `FakeLLM` mimics it offline).

**One execution seam.** Every call — from any backend — goes through
[`Tool.run`](../../src/assistant/agent/tools/base.py), which is where all telemetry
lives:

1. opens an OTel span `tool.execute` (attrs: `tool.name`, `tool.status`,
   `tool.result_chars`),
2. times the call into the `assistant_tool_seconds{tool}` histogram and counts
   it in `assistant_tool_calls_total{tool,status}`,
3. writes a structured `tool.executed` log line (with the current
   `session_id` / `turn_id` bound automatically),
4. converts a crash into an **error result** — the string
   `error: tool '<name>' failed: <exc>` goes back to the model, and the agent
   loop keeps running. A tool can never kill a turn.

`status` is `ok`, `error` (handler returned an `error:`-prefixed string),
`crash` (handler raised), `duplicate` (see below), or `unknown` (model
hallucinated a tool name).

**Duplicate-call guard.** Models (llama especially) sometimes repeat the
exact same tool call within one turn — re-fetching the same URL three times
burns seconds, tokens, and the free-tier rate limit for zero new information.
`Tool.run` keeps a per-turn set of `(tool, arguments)` and answers repeats
with a short "you already ran this — use the earlier result" message instead
of re-executing. Fresh turn, fresh set; different arguments always execute.

**How each backend consumes the registry.**

- *custom* ([backends/custom.py](../../src/assistant/agent/backends/custom.py)) —
  its loop passes `registry.specs` to the LLM step and executes
  `ToolCallRequest`s via `registry.execute(name, args)`.
- *pydantic_ai* ([backends/pydantic_ai.py](../../src/assistant/agent/backends/pydantic_ai.py))
  — each registry tool is adapted with `Tool.from_schema(...)`; the adapter
  calls `tool.run`, so telemetry is identical.
- *langgraph* ([backends/langgraph.py](../../src/assistant/agent/backends/langgraph.py))
  — tools surface through the `LLMClientChatModel` adapter and are executed
  against the same registry.

**Native vs MCP.** `search_docs` and `fetch_url` are native (constructed in-process). The
remaining five arrive over **MCP**: at startup
[`MCPRegistry`](../../src/assistant/mcp/registry.py) spawns each configured
server (default: two local stdio subprocesses, `{python}` resolving to the
current interpreter), lists its tools, and wraps each one as a registry
`Tool` named `<server>__<tool>` — the backends cannot tell the difference.
Connect timeout is 15 s, per-call timeout 60 s; an unreachable server is
logged and skipped, and the agent runs with whatever connected (graceful
degradation). Servers are configured via `ASSISTANT_MCP_SERVERS` JSON
(see `.env.example`); the defaults need zero credentials.

---

## Tool catalog

### `search_docs` — RAG over the internal docs (native)

| | |
|---|---|
| Purpose | Answer questions from the knowledge base — whatever documents were added via the UI Documents panel, `POST /api/documents`, or the ingest CLI. Starts empty. |
| Parameters | `query` *(string, required)* — natural-language search query |
| Returns | Up to 4 chunks, each as `[source.md — heading] (score 0.87)` + chunk text (truncated at 1500 chars), separated by `---`. `NOTHING_INDEXED` when the knowledge base is empty, `NO_RELEVANT_DOCS` when nothing matches |
| Errors | `error: the 'query' argument is required` on an empty query |
| Implementation | [`make_search_docs`](../../src/assistant/agent/tools/search_docs.py) → [`Retriever.search`](../../src/assistant/rag/retriever.py) |

Under the hood the retriever embeds the query (hash embedder by default,
OpenAI/Voyage when configured), runs **hybrid** search in Qdrant (dense +
sparse lexical vectors fused with RRF), optionally reranks the top-20 with the
lexical reranker, and returns the top-4. The whole thing is wrapped in an OTel
span `rag.retrieve` (mode, candidates, results, top score) nested inside the
tool's `tool.execute` span, plus a `rag.retrieved` log line and the
`assistant_retrieval_seconds{mode}` histogram.

**Relevance gate.** Vector search always returns *something*, and RRF/hash
scores are not calibrated — so the tool drops chunks that share no meaningful
token with the query (`query_overlap` in [rerank.py](../../src/assistant/rag/rerank.py),
prefix-tolerant: "deploy" matches "deployment"). If nothing survives, the
model gets the live inventory of indexed sources, filename matches for the
query's tokens, and a retry contract (two more phrasings, then report what
was searched) — instead of confident-looking noise, and instead of a
one-try surrender.

The system prompt tells the model to call this tool first for any question
about internal systems and to cite the source files it gets back.

### `fetch_url` — public web pages & GitHub (native)

| | |
|---|---|
| Purpose | Ground answers about external URLs/projects in real page content instead of guessing |
| Parameters | `url` *(string, required)* — absolute http(s) URL |
| Returns | Readable text of the page (HTML stripped), capped at 8 000 chars. **GitHub special cases** via the API: `github.com/{owner}/{repo}` → description, language, stars, topics + README (first 6 000 chars); `github.com/{owner}` → account info + list of public repositories |
| Errors | `error: only http(s) URLs are supported`, `error: refusing to fetch private or loopback addresses`, `error: GET <url> returned HTTP <status>`, `error: could not fetch <url>: <why>` |
| Implementation | [`make_fetch_url`](../../src/assistant/agent/tools/fetch.py) — httpx, 15 s timeout, redirects followed |

Notes: the loopback/private-range block is a **dev-grade** SSRF guard (string
match on the host; production would resolve DNS and enforce an egress
allowlist). GitHub API calls are unauthenticated (60 req/h per IP) — enough
for chat use; on rate-limit the tool falls back to fetching the HTML page.

### `ingest_repo` — add a GitHub repo's docs to the knowledge base (native, **the one write**)

| Parameter | Type | Required | Meaning |
|---|---|---|---|
| `repo` | string | yes | `owner/repository`, e.g. `thechekh/demo-payments-platform` |
| `ref` | string | no | branch/tag/SHA; default branch when omitted |

Fetches every `.md`/`.txt`/`.rst` in the repository (≤100 files, ≤2 MB each) —
and with `include_code=true` also its source files (`.py`/`.ts`/`.go`/…,
≤300 KB each; lockfiles, `node_modules` and minified bundles are skipped) —
and indexes them as `owner/repo/path` sources — re-running refreshes that
repo's documents in place, and two repos can never overwrite each other.
Returns the indexed source list so the model can cite what it just learned;
failures come back as `error:` text (missing repo, no docs, bad name).
Additive only — it cannot delete or modify anything, which
`test_review_regressions.py` pins as the sole exception to the otherwise
read-only tool surface. Uses `ASSISTANT_GITHUB_TOKEN` for private repos.
*Implementation:* [agent/tools/ingest_repo.py](../../src/assistant/agent/tools/ingest_repo.py),
[rag/repo.py](../../src/assistant/rag/repo.py).

### `repo_read_file` — one exact file from any GitHub repo (native)

| Parameter | Type | Required | Meaning |
|---|---|---|---|
| `repo` | string | yes | `owner/repository` |
| `path` | string | yes | file path inside the repo, e.g. `services/payments/adapter.py` |
| `ref` | string | no | branch/tag/SHA; default branch when omitted |

The other half of the code story: `ingest_repo(include_code=true)` makes a
repo's source searchable, `search_docs` finds the chunk (source =
`owner/repo/path`), and this opens the full file so the answer can show real
code. **Public repositories need no token** — the project's value never
hinges on a PAT; `ASSISTANT_GITHUB_TOKEN` extends it to private repos.
Read-only; one validated GET against `api.github.com` (the model never
supplies a URL); long files are truncated by the shared 20k result cap.
*Implementation:* [agent/tools/repo_read.py](../../src/assistant/agent/tools/repo_read.py),
[rag/repo.py](../../src/assistant/rag/repo.py).

### `code__search_code` — regex search over a repository (MCP: `code` server)

| | |
|---|---|
| Purpose | Find code/config/doc lines in the repository the server is rooted at |
| Parameters | `pattern` *(string, required)* — regex, case-insensitive; `max_results` *(int, default 20)* |
| Returns | One `path:line: content` per match (content trimmed to 200 chars), or `no matches for '<pattern>'` |
| Errors | `error: invalid regex '<pattern>': <why>` |
| Implementation | [`mcp_servers/code_search.py`](../../src/assistant/mcp_servers/code_search.py), pure Python (no ripgrep dependency) |

The search root is `CODE_SEARCH_ROOT` (env of the *server subprocess*;
default = the API server's working directory, i.e. this repository). It walks
the tree skipping `node_modules`, `__pycache__`, `dist`, `build` and dot-dirs,
reads only known text extensions (`.py .ts .tsx .js .vue .md .toml .yaml .yml
.json .html .css .txt .cfg .ini .sql`) under 512 KB, and returns at most
`max_results` hits. To search a *different* repo, set `CODE_SEARCH_ROOT` in
that server's `env` block in `ASSISTANT_MCP_SERVERS`.

### `code__read_file` — read a file from the repository (MCP: `code` server)

| | |
|---|---|
| Purpose | Let the model open a file it found via `search_code` |
| Parameters | `path` *(string, required, relative to the repo root)*; `start_line` *(int, default 1)*; `max_lines` *(int, default 100)* |
| Returns | `N: line` numbered lines for the requested window |
| Errors | `error: path escapes the repository root` (traversal guard), `error: no such file: <path>`, `(no lines at N+ — the file has M lines)` |
| Implementation | same server file as above |

### `github__list_pull_requests` — list PRs (MCP: `github` server, **mocked**)

| | |
|---|---|
| Purpose | Demo "workspace" data: 5 canned PRs with realistic states |
| Parameters | `state` *(string, default `"open"`; `open`/`merged`/`all`/`any`)*; `limit` *(int, default 5)* |
| Returns | One line per PR: `#142 [open] title — by author, CI status, reviews, updated date` |
| Implementation | [`mcp_servers/fake_github.py`](../../src/assistant/mcp_servers/fake_github.py) — static in-memory data |

### `github__get_pull_request` — one PR in detail (MCP: `github`, **mocked**)

| | |
|---|---|
| Parameters | `number` *(int, required)* |
| Returns | Title, state/branch/author, checks/reviews/updated, full description body |
| Errors | `error: pull request #N not found` |

### `github__list_issues` — list issues (MCP: `github`, **mocked**)

| | |
|---|---|
| Parameters | `state` *(string, default `"open"`)*; `limit` *(int, default 10)* |
| Returns | `#135 [open] title` lines from 3 canned issues |

**Why a mock?** It borrows tool names from the official GitHub MCP
server (`ghcr.io/github/github-mcp-server`; one has since been renamed
upstream, harmlessly — tools are discovered at startup), so switching to
real GitHub is config-only — put a PAT into the docker-based server entry shown in
`.env.example` (`ASSISTANT_MCP_SERVERS`) and the agent's behavior carries over
unchanged.

---

## Offline demo triggers (FakeLLM)

With the default `fake` provider there's no real model, so
[`FakeLLM`](../../src/assistant/llm/client.py) plays a one-round agent on keyword
heuristics — useful when testing tools without a key:

| You type | Tool it calls |
|---|---|
| anything mentioning *PR*, *PRs* or *pull request* | `github__list_pull_requests` |
| `search code for <pattern>` | `code__search_code` with that pattern |
| any message containing an `http(s)://` URL | `fetch_url` with that URL |
| any message ending in `?` | `search_docs` with the full question |
| anything after a tool result | final answer quoting the result |

A real model (e.g. OpenAI) chooses tools from the descriptions above on its own.

**Reliability on OpenAI/llama.** llama models sometimes fumble the tool-call
protocol: they emit the call as plain text (`<function.name>{…}</function>`,
`<function=name>{…}`, `<function(name){…}`) or trip OpenAI's `tool_use_failed`
stream error. [`OpenAICompatibleLLM`](../../src/assistant/llm/client.py) absorbs
both: leaked text is held back and parsed into real tool calls
(`parse_leaked_tool_calls`), failed steps are retried up to 2× and, as a last
resort, the call is recovered from OpenAI's `failed_generation` payload. The
agent loop and the UI only ever see proper tool calls.

## What a tool call looks like in observability

One `search_docs` call, across every surface:

- **UI** — a dashed tool card (name, args, result) while streaming; the tool
  name in the stats line under the answer; a `tool_call` / `tool_result` row
  (with `+ms` offsets) in the *details* timeline.
- **Logs** — `tool.executed {tool, status, duration_ms, result_chars}` and
  `rag.retrieved {mode, results, top_source, top_score}` — both carrying
  `session_id` / `turn_id` / `backend`.
- **Trace (Jaeger)** — `agent.turn` → `llm.step` → `tool.execute` →
  `rag.retrieve` waterfall with timings and attributes.
- **Metrics** — `assistant_tool_calls_total{tool="search_docs",status="ok"}`,
  `assistant_tool_seconds`, `assistant_retrieval_seconds`.
- **Audit** — the turn record in `GET /api/sessions/{id}/turns` with the
  event timeline.

## Adding a tool

**Native:** build a `Tool` (name, description the model will read, JSON
Schema, async handler returning `str`; prefix failures with `error:`) and add
it to `native_tools` in `build_runtime()`. Telemetry comes
free via `Tool.run`.

**MCP:** write a server with `FastMCP` + `@mcp.tool()` functions (see
`code_search.py` — docstrings become tool descriptions, type hints become the
schema), then add it to `ASSISTANT_MCP_SERVERS`. Or point at any third-party
MCP server (stdio command or streamable-HTTP URL) — its tools appear as
`<name>__<tool>` automatically.
