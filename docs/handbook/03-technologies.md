# 03 — Every technology: what, why, where

**What this chapter covers: the full stack, one entry per technology — what
it does here, why it was chosen over the alternatives, and where to look in
the code.** It is not the design rationale in full — see
[tech-stack.md](../project/tech-stack.md) for the alternatives and the
reasoning, and [theory/](../theory/README.md) for concept-level explanations
— this page is the map from a name to a file.

## 1. Backend

### Python 3.12 + uv + ruff + pyright
The toolchain. **uv** manages the venv and lockfile (`uv sync`, `uv run` —
fast, reproducible; chosen over poetry, pip+venv and pdm for speed and a
single tool that also manages Python versions), **ruff** lints and formats
(line length 100; one tool replacing black+flake8+isort), **pyright**
type-checks strictly (chosen over mypy for speed and the same inference
engine as VS Code). CI runs exactly these plus pytest. Measured 2026-09-05 on
this machine: `uv run ruff check . -q` exits clean in 0.78 s and
`uv run ruff format --check . -q` in 0.13 s — "instant" above is not a figure
of speech.
*Where:* [pyproject.toml](../../pyproject.toml), [.github/workflows/ci.yml](../../.github/workflows/ci.yml).

### FastAPI + uvicorn
The web framework and ASGI server: one process serves the WebSocket chat
(`/chat`), the REST API (`/api/*`), Prometheus metrics (`/metrics`), and the
built Vue UI (static files at `/`). Chosen for first-class async +
WebSockets + pydantic integration + auto OpenAPI (`/docs`) — the project
brief named FastAPI directly, so this was not a bake-off; Litestar was noted
as a credible alternative and granian as a drop-in uvicorn replacement if
raw throughput ever mattered.
`python-multipart` rides along as a direct dependency because Starlette needs
it to parse the file uploads `POST /api/documents` accepts — without it that
endpoint fails at import time rather than at request time.
*Where:* [main.py](../../src/assistant/main.py) (app factory + lifespan wiring),
[api/ws.py](../../src/assistant/api/ws.py), [api/routes.py](../../src/assistant/api/routes.py).

### Pydantic v2 + pydantic-settings
Every boundary is typed: WS frames, config, session records. Settings load
from `.env`/environment with the `ASSISTANT_` prefix; secrets are `SecretStr`
so they can't leak into logs. Chosen over dynaconf, environs or raw
`os.environ` for validation at startup and the same ecosystem FastAPI already
uses.
*Where:* [config.py](../../src/assistant/config.py), [api/schemas.py](../../src/assistant/api/schemas.py),
[agent/base.py](../../src/assistant/agent/base.py).

### openai SDK (the *client*, not the provider)
One `AsyncOpenAI` client speaks to **every** hosted provider — OpenAI,
Ollama, Gemini — because they all expose the OpenAI-compatible chat API; the
provider is just a `base_url` + key. The whole table is three lines:
`PROVIDER_BASE_URLS = {"openai": None, "ollama": "http://localhost:11434/v1",
"gemini": "https://generativelanguage.googleapis.com/v1beta/openai/"}`
([llm/client.py](../../src/assistant/llm/client.py)) — `None` means "let the
SDK use its own default." Streaming, tool calls, and usage reporting ride the
same code path everywhere.
*Where:* [llm/client.py](../../src/assistant/llm/client.py) (`PROVIDER_BASE_URLS`).

### Redis (redis-py asyncio) / fakeredis
Session transcripts, the rolling summary, and the per-turn audit trail — all
keyed by `session_id` with a 24 h TTL. Chosen over in-process dicts (lost on
restart) or Postgres (nothing else in this project needs a relational store).
`ASSISTANT_REDIS_URL=fakeredis://` swaps in an in-memory clone for
zero-infra dev; tests always use it.
*Where:* [memory/session.py](../../src/assistant/memory/session.py).

### Qdrant (+ qdrant-client)
The vector database: one collection (`docs`) holding **named vectors** — a
dense vector (512-dim with the offline hash embedder, 1536-dim with OpenAI)
and a sparse lexical vector per chunk — enabling hybrid
search with server-side RRF fusion. Chosen over Weaviate (heavier, no
advantage here), Chroma (prototyping only), pgvector (no other Postgres need)
and LanceDB (smaller ecosystem) for hybrid search and payload filtering being
native rather than bolted on. In tests the same client runs fully in-memory
(`:memory:`). The fixture corpus embeds into exactly 30 points (5 files,
[evals/corpus/](../../evals/corpus/), measured 2026-09-05 — chapter 05 has
the reproduction command). Web UI at `localhost:6333/dashboard`.
*Where:* [rag/store.py](../../src/assistant/rag/store.py); chapter 05.

### MCP SDK (`mcp`)
The Model Context Protocol client and server. The app spawns two bundled
stdio servers (code search, GitHub mock) and adapts their tools into the
shared registry — each tool is namespaced `server__tool`, e.g.
`code__search_code` — and any third-party MCP server plugs in via config
JSON.
*Where:* [mcp/registry.py](../../src/assistant/mcp/registry.py),
[mcp_servers/](../../src/assistant/mcp_servers/); chapter 06.

### Agent frameworks: Pydantic AI + LangGraph (+ the custom loop)
Three implementations of one small `AgentBackend` protocol, switchable per
session — built to *compare* frameworks against a hand-written loop with
identical behavior, not to pick a winner up front. The custom loop is the
reference; pydantic-ai brings its own model layer and typed tools; langgraph
models the loop as a state graph. Plain LangChain agents (legacy API
surface) and CrewAI/AutoGen (multi-agent focus, out of scope here) were
ruled out before the comparison began.
Measured (`wc -l`, 2026-09-05): the custom loop is **98** lines, Pydantic
AI's backend **286**, LangGraph's **278** —
[backend-comparison.md](../reference/backend-comparison.md) is the full
measured comparison, line counts included.
*Where:* [agent/backends/](../../src/assistant/agent/backends/).

## 2. Observability

### structlog
Structured logging: pretty console in dev, JSON lines with
`ASSISTANT_LOG_JSON=true`. One pipeline also reformats stdlib/uvicorn logs,
and `structlog.contextvars` injects `session_id`/`turn_id`/`backend` into
**every** line automatically. One real line, keyword arguments and all:
`rag.retrieved mode=hybrid results=4 duration_ms=1003`
([rag/retriever.py](../../src/assistant/rag/retriever.py)) — every field
after the event name is a keyword argument to the logger call, not string
formatting, which is what makes it greppable and JSON-safe at the same time.
*Where:* [logs.py](../../src/assistant/logs.py); chapter 07.

### OpenTelemetry — `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`
Three packages with distinct jobs, which is why all three are direct
dependencies rather than transitive ones: `opentelemetry-api` is what
`telemetry.py` imports to create spans (it is a no-op on its own, so
instrumented code needs no guards), `opentelemetry-sdk` is the implementation
`observability.py` installs only when a destination exists, and
`…-exporter-otlp-proto-http` ships the spans over HTTP.

Manual spans on the four seams that explain the agent: `agent.turn`,
`llm.step`, `tool.execute`, `rag.retrieve`. Inert (no-op tracer) until a
destination is configured: local Jaeger via `ASSISTANT_OTLP_ENDPOINT`, and/or
Logfire/Langfuse cloud.
*Where:* [telemetry.py](../../src/assistant/telemetry.py) (spans),
[observability.py](../../src/assistant/observability.py) (destinations).

### prometheus-client + Prometheus + Grafana
Counters and histograms (turns, latency, tool calls, tokens, cost, errors)
served at `/metrics`; the compose `observability` profile runs Prometheus
(scrapes every 5 s) and Grafana with a pre-provisioned dashboard. One real
series to try: `curl -s localhost:8000/metrics | grep assistant_cost_usd_total`
— the same counter chapter 04 reads for spend.
*Where:* [telemetry.py](../../src/assistant/telemetry.py),
[observability/](../../observability/).

### Jaeger (all-in-one)
Local trace UI — zero accounts. Receives OTLP on :4318, UI on :16686.
*Where:* [docker-compose.yml](../../docker-compose.yml) `observability` profile.

## 3. Frontend

### Vue 3 + Pinia + Vite + TypeScript
Composition-API SPA — chosen over a single-file HTML page or Streamlit for a
real SPA experience with WS streaming (user preference, recorded in
[tech-stack.md §13](../project/tech-stack.md)): a Pinia store owns the
WebSocket (via `@vueuse/core`'s `useWebSocket`, auto-reconnect with the same
session), typed mirrors of the WS protocol, markdown rendering (markdown-it,
HTML escaping on), tool cards, per-turn stats line, the "details" audit
timeline, and the deep-health dot.
*Where:* [frontend/src/](../../frontend/src/) — start at
[stores/chat.ts](../../frontend/src/stores/chat.ts).

## 4. Testing & quality

### pytest + pytest-asyncio + fakeredis + in-memory Qdrant
Deterministic tests, no network, no Docker: the WS suite runs across all
three backends via parametrized fixtures; provider quirks (429s, a provider's
`tool_use_failed`, leaked tool syntax) are reproduced with scripted fakes.
Coverage is a ratchet, not a snapshot: `fail_under = 84` in
[pyproject.toml](../../pyproject.toml), raised as real coverage grew and
never lowered.
*Where:* [tests/](../../tests/); chapter 09.

### GitHub Actions
Two workflows on every push. **CI**: ruff → format check → pyright →
pytest with a coverage floor, across Python 3.12 **and** 3.13, plus a
frontend job (typecheck → vitest → build) and a Docker image build.
**Security**: CodeQL, `pip-audit` and `npm audit`, weekly as well as on push.
*Where:* [.github/workflows/ci.yml](../../.github/workflows/ci.yml).

## 5. Docker Compose profiles

| Command | Services |
|---|---|
| `docker compose up -d` | redis, qdrant (dev default) |
| `docker compose --profile observability up -d` | + jaeger, prometheus, grafana |
| `docker compose --profile app up --build` | + api (everything in containers) |

Every image is version-pinned rather than `:latest`
([docker-compose.yml](../../docker-compose.yml)): `qdrant/qdrant:v1.19.0`,
`jaegertracing/all-in-one:1.62.0`, `prom/prometheus:v3.1.0`,
`grafana/grafana:11.5.1` — so a stack brought up today and one brought up in
six months run the same versions until someone deliberately bumps them.

## 6. Showing it live

One technology swap, live, about ten seconds:

1. Run `uv run ruff check . && uv run ruff format --check .` — *"one tool,
   lint and format, both clean."* (0.78 s and 0.13 s measured 2026-09-05 on
   this machine — §1 above.)
2. With Mode A running (chapter 02), switch the UI's backend dropdown from
   **custom** to **pydantic_ai** to **langgraph** and ask the same question
   each time — *"three files, 98, 286 and 278 lines, same tool registry,
   same answer."*
3. `curl -s localhost:8000/metrics | grep assistant_cost_usd_total` — *"one
   counter, whichever provider or framework is behind it."*

## 7. Reading it honestly

- **This page lists what runs today, not what was tried.** taskiq
  (background jobs) shipped in Phase 8 and was removed in full once its only
  job — a nightly corpus re-index — stopped existing, because ingestion now
  happens at upload time.
  [future-tools.md §4](../project/future-tools.md) and
  [tech-stack.md §10](../project/tech-stack.md) keep the record of why it
  was the right pick for a job that turned out not to exist.
- **"Chosen for X" is not "the only option that works."** pgvector, Chroma
  and LanceDB would all run this project's RAG; Qdrant won on hybrid search
  and payload filtering being native, not on being the only vector store
  capable of the job.
- **The framework comparison is graded on this project's scope** — a
  single-tenant chat assistant with short tool loops.
  [backend-comparison.md §8](../reference/backend-comparison.md) is explicit
  that a different scope (durable checkpoints, multi-agent graphs) would
  rank Pydantic AI and LangGraph differently.
- **Version pins drift.** The exact versions in
  [pyproject.toml](../../pyproject.toml) and
  [docker-compose.yml](../../docker-compose.yml) are correct as of
  2026-09-05; a reader should check those files rather than trust a number
  copied here indefinitely.

## 8. Related

- [01 — Project overview](01-project-overview.md) — how these technologies compose into one running system
- [02 — Getting started](02-getting-started.md) — running every technology on this page, mode by mode
- [project/tech-stack.md](../project/tech-stack.md) — the alternatives that lost, and why, for every decision on this page
- [project/future-tools.md](../project/future-tools.md) — technologies evaluated and deliberately not shipped
- [reference/backend-comparison.md](../reference/backend-comparison.md) — the three agent frameworks, measured against each other
- [theory/README.md](../theory/README.md) — the concepts behind each technology, without the file paths
