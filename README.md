# AI Workspace Assistant

Internal AI assistant for engineers: agentic FastAPI backend, MCP tools, Qdrant RAG, real-time WebSocket chat.

📚 **All documentation lives in [docs/](docs/README.md)** — one source of truth:
[handbook](docs/handbook/README.md) (run & operate it) ·
[theory](docs/theory/README.md) (concepts from zero) ·
[reference](docs/reference/tools.md) (tools, testing, backend comparison) ·
[project](docs/project/implementation-plan.md) (history, decisions, workshop)

## Quickstart

```sh
# 1. Install backend dependencies (creates .venv)
uv sync

# 2. Build the frontend once (for the UI at /)
cd frontend && npm install && npm run build && cd ..

# 3. Start infrastructure (Redis for sessions; Qdrant used from Phase 2)
docker compose up -d
#    No Docker? Skip this and set ASSISTANT_REDIS_URL=fakeredis:// in .env
#    (in-memory sessions — zero setup, lost on restart)

# 4. Run the API (fake offline LLM by default — no API key needed)
uv run uvicorn assistant.main:app --reload

# 5. Open the app
#    http://localhost:8000/      — Vue chat UI (served from frontend/dist)
#    http://localhost:8000/dev   — minimal built-in dev console
```

## Frontend development

```sh
cd frontend
npm run dev        # Vite dev server on :5173 with hot reload,
                   # proxies /chat (WebSocket) to the API on :8000
npm run build      # type check (vue-tsc) + production build → frontend/dist
```

## RAG

The knowledge base starts **empty** — no seed data ships with the app. Add
the documents it should answer from at runtime:

- **In the UI**: the **Documents** panel in the header — drop `.md`/`.txt`/
  `.rst` files or paste text. Searchable on your next message.
- **Over HTTP**: `POST /api/documents` (multipart `files=` and/or
  `text=`+`source=`), `GET /api/documents` to list, `DELETE
  /api/documents/{source}` to remove.
- **From a GitHub repository, by asking in chat**: *"ingest the docs from
  owner/name"* — the agent's `ingest_repo` tool pulls every `.md`/`.txt`/`.rst`
  and indexes it as `owner/repo/path`, so two repos' files can never collide.
  It is the agent's only write capability (additive, pinned by a test).
  Public repos need no token; `ASSISTANT_GITHUB_TOKEN` unlocks private ones.
- **From a folder**: `uv run python -m assistant.rag.ingest <folder>` — a
  one-off CLI, run when you choose to.

Re-uploading the same source replaces it — chunk ids are deterministic.

```sh
# retrieval quality against the golden question set (fixture: evals/corpus/)
uv run python evals/run_retrieval.py --memory            # --memory: no Docker needed
uv run python evals/run_retrieval.py --memory --check    # fail if quality regressed (CI runs this)
uv run python evals/run_retrieval.py --trend             # recorded history

# compare embedding models (hash always; openai/voyage when keys are set)
uv run python -m evals.compare_embeddings         # -> evals/results-embeddings.md
```

Retrieval is **hybrid** (dense + sparse lexical vectors, RRF fusion) with a
deterministic lexical reranker over the top-20. Measured on the golden set
with the free offline `hash-512` embedder (18 questions):

| config | recall@1 | recall@5 | MRR |
|---|---:|---:|---:|
| dense only, no rerank | 0.78 | 0.94 | 0.86 |
| hybrid, no rerank | 0.72 | 1.00 | 0.86 |
| dense + rerank | 0.89 | 1.00 | 0.94 |
| hybrid + rerank (default) | **0.83** | **1.00** | **0.92** |

For real embeddings set `ASSISTANT_EMBEDDING_PROVIDER=openai` (+ key) or
`voyage` (+ `ASSISTANT_VOYAGE_API_KEY`) and re-ingest.

## Conversation memory

The full transcript lives in Redis; what the agent sees is bounded. When the
un-summarized history exceeds `ASSISTANT_HISTORY_CHAR_BUDGET`, older turns
fold into a persisted rolling summary (each message summarized at most once)
and the last `ASSISTANT_HISTORY_KEEP_RECENT` messages stay verbatim — so
prompts stop growing on long conversations, identically on all three
backends. Offline the summary is extractive; with a real LLM provider the
model maintains it.

## Agent backends

Interchangeable runtimes implement the same `AgentBackend` protocol and get
the same tools. Default via `ASSISTANT_AGENT_BACKEND`; switch per session
with the UI dropdown (WS `?backend=` — reconnects keeping your history):

- `custom` — the hand-written ReAct loop (no framework)
- `pydantic_ai` — Pydantic AI (graph iteration API)
- `langgraph` — LangGraph state graph with per-turn checkpointing

Measured three-way comparison: [backend comparison](docs/reference/backend-comparison.md).

## Observability

Layered, offline-first — everything below the cloud backends needs **zero
accounts**:

**Structured logs (always on).** structlog renders pretty console lines in
dev, JSON lines with `ASSISTANT_LOG_JSON=true`. Every log from the agent
loop, tools, RAG, and WS layer automatically carries `session_id`,
`turn_id`, and `backend`; each turn ends with one greppable `turn.summary`
line (duration, first-token latency, LLM steps, tool calls, tokens).
`ASSISTANT_LOG_PROMPTS=true` additionally dumps full prompts/completions
(dev-only — conversations end up in logs).

**Metrics (always on).** `GET /metrics` exposes Prometheus counters and
histograms: turns + latency by backend, LLM step latency by provider, tool
calls by tool/status, retrieval latency by mode, token totals, errors.

**Per-turn stats in the UI.** After each answer the server sends a `turn`
WS frame; the UI renders it as a stats line under the message (duration,
first token, LLM steps, tokens — real from `stream_options.include_usage`
when the provider reports them, estimated otherwise — and tools used). The
header shows a deep-health dot (`GET /api/health`: Redis ping, Qdrant
count, MCP servers). Each session's full event timeline is served at
`GET /api/sessions/{id}/turns` (last 50 turns, Redis TTL).

**Traces, no accounts.** Manual OTel spans on the seams that explain the
agent — `agent.turn` → `llm.step` / `tool.execute` / `rag.retrieve`:

```sh
docker compose --profile observability up -d   # Jaeger + Prometheus + Grafana
# .env: ASSISTANT_OTLP_ENDPOINT=http://localhost:4318
# Jaeger UI   http://localhost:16686 — trace waterfall per turn
# Grafana     http://localhost:3000  — provisioned dashboard, no login
# Prometheus  http://localhost:9090
```

**Cloud backends (optional).** The same spans also export to Logfire
(`ASSISTANT_LOGFIRE_TOKEN` — adds FastAPI/httpx/pydantic-ai
auto-instrumentation) and/or Langfuse (`ASSISTANT_LANGFUSE_*` keys —
generations + costs view). With no destination configured, tracing is
fully inert: no SDK imports, no network, no-op tracer.

## MCP tools

The agent's tools come from MCP servers (plus the native `search_docs`).
Two bundled stdio servers start automatically — **no credentials needed**:

- `code` — regex code search + file reading over this repository
  (`search code for class CustomAgent`)
- `github` — a **mocked** GitHub server with canned PRs/issues
  (`Show latest PRs in the repo`)

The mock borrows the official GitHub MCP server's tool names (two of three
still match upstream — `get_pull_request` was renamed `pull_request_read`
there), and because tools are discovered at startup, switching to real GitHub
is just the `ASSISTANT_MCP_SERVERS` JSON in
`.env` (see `.env.example`) — no code changes. Unreachable servers are
skipped with a warning; the agent keeps running with the tools it has.

## Using a real (free) model

```sh
cp .env.example .env
# set in .env:
#   ASSISTANT_LLM_PROVIDER=openai
#   ASSISTANT_LLM_API_KEY=sk-...   # free key from platform.openai.com/api-keys
```

## Development

```sh
uv run pytest -q            # tests
uv run ruff check .         # lint
uv run ruff format .        # format
uv run pyright              # type check
uv run pre-commit install   # install git hooks (once)
```

## Full platform

```sh
# Entire platform in containers (api + redis + qdrant):
docker compose --profile app up --build
```

The knowledge base starts empty and stays that way until documents are added
— through the UI, `POST /api/documents`, or the ingest CLI. Nothing pre-loads
it and nothing re-indexes on a schedule: an uploaded document is embedded once
at upload, so there is no batch to run.

Optional auth: set `ASSISTANT_AUTH_TOKEN` — `/api/*` then requires a bearer
header and the chat WS a `?token=`; open the UI once as
`http://localhost:8000/?token=<token>` and it persists the token locally.

## Project layout

```
src/assistant/
├── main.py            # create_app() factory, lifespan wiring, /healthz, /dev
├── config.py          # pydantic-settings (ASSISTANT_* env vars)
├── api/               # WS /chat + turn recorder; HTTP: info, health,
│                   #   documents, sessions/turns, metrics
├── agent/             # AgentBackend protocol, events, backends/ (custom | pydantic_ai | langgraph)
├── llm/               # provider-agnostic LLM client (fake | openai | ollama | gemini)
├── memory/            # Redis-backed session history
├── mcp/               # MCP client registry (stdio/http, tool namespacing)
├── mcp_servers/       # bundled MCP servers: code_search + mocked fake_github
├── rag/               # chunking, embedders, Qdrant store, retriever, ingest CLI
└── static/dev.html    # minimal WS dev console
docs/                  # ALL documentation (see docs/README.md)
├── handbook/          #   operating this project — 9 chapters
├── theory/            #   every concept from zero — 12 chapters
├── reference/         #   tools, testing checklist, backend comparison, security
└── project/           #   roadmap/TODO, tech decisions, build history, workshop
evals/                 # golden question set, retrieval metrics runner, and
└── corpus/            #   the fixture those questions are measured on
frontend/              # Vue 3 + Vite + TS SPA (Pinia, @vueuse/core, markdown-it)
observability/         # Prometheus config + provisioned Grafana dashboard
tests/                 # pytest (fake LLM + fakeredis + in-memory Qdrant)
```
