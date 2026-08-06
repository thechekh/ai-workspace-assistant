# AI Workspace Assistant

Internal AI assistant for engineers: agentic FastAPI backend, MCP tools, Qdrant RAG, real-time WebSocket chat.

Docs: [project description](project-description-en.md) · [tech stack & decisions](tech-stack.md) · [implementation plan](implementation-plan.md) · [roadmap / TODO](TODO.md) · [backend comparison](docs/backend-comparison.md) · [workshop materials](docs/workshop.md) · **[theory — every concept from zero](theory/README.md)**

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

```sh
# ingest the sample corpus into Qdrant (offline hash embedder by default — zero cost)
uv run python -m assistant.rag.ingest docs_corpus --recreate

# measure retrieval quality against the golden question set
uv run python evals/run_retrieval.py --memory     # --memory: no Docker needed

# compare embedding models (hash always; openai/voyage when keys are set)
uv run python -m evals.compare_embeddings         # -> evals/results-embeddings.md
```

Retrieval is **hybrid** (dense + sparse lexical vectors, RRF fusion) with a
deterministic lexical reranker over the top-20. Measured on the golden set
with the free offline `hash-512` embedder (18 questions):

| config | recall@1 | recall@5 | MRR |
|---|---:|---:|---:|
| dense only (Phase 2 baseline) | 0.56 | 0.94 | 0.72 |
| hybrid | 0.67 | 1.00 | 0.80 |
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

Measured three-way comparison: [docs/backend-comparison.md](docs/backend-comparison.md).

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

The mock exposes the same tool names as the official GitHub MCP server, so
switching to real GitHub later is just the `ASSISTANT_MCP_SERVERS` JSON in
`.env` (see `.env.example`) — no code changes. Unreachable servers are
skipped with a warning; the agent keeps running with the tools it has.

## Using a real (free) model

```sh
cp .env.example .env
# set in .env:
#   ASSISTANT_LLM_PROVIDER=groq
#   ASSISTANT_LLM_API_KEY=gsk_...   # free key from console.groq.com
```

## Development

```sh
uv run pytest -q            # tests
uv run ruff check .         # lint
uv run ruff format .        # format
uv run pyright              # type check
uv run pre-commit install   # install git hooks (once)
```

## Background jobs & full platform

Document re-indexing runs as a **taskiq** job (nightly cron at 03:00, plus
the UI's Re-index button / `POST /api/reindex`). In zero-infra mode
(`fakeredis://`) the reindex runs inline instead of queuing.

```sh
uv run taskiq worker assistant.worker:broker        # job worker
uv run taskiq scheduler assistant.worker:scheduler  # nightly cron

# Entire platform in containers (api + worker + scheduler + redis + qdrant):
docker compose --profile app up --build
```

Optional auth: set `ASSISTANT_AUTH_TOKEN` — `/api/*` then requires a bearer
header and the chat WS a `?token=`; open the UI once as
`http://localhost:8000/?token=<token>` and it persists the token locally.

## Project layout

```
src/assistant/
├── main.py            # create_app() factory, lifespan wiring, /healthz, /dev
├── config.py          # pydantic-settings (ASSISTANT_* env vars)
├── api/               # WS protocol schemas + /chat endpoint
├── agent/             # AgentBackend protocol, events, backends/ (custom | pydantic_ai | langgraph)
├── llm/               # provider-agnostic LLM client (fake | groq | ollama | gemini | openai)
├── memory/            # Redis-backed session history
├── mcp/               # MCP client registry (stdio/http, tool namespacing)
├── mcp_servers/       # bundled MCP servers: code_search + mocked fake_github
├── rag/               # chunking, embedders, Qdrant store, retriever, ingest CLI
└── static/dev.html    # minimal WS dev console
docs_corpus/           # sample internal docs (demo + eval corpus)
evals/                 # golden question set + retrieval metrics runner
frontend/              # Vue 3 + Vite + TS SPA (Pinia, @vueuse/core, markdown-it)
tests/                 # pytest (fake LLM + fakeredis + in-memory Qdrant)
```
