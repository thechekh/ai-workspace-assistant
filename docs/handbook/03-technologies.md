# 03 — Every technology: what, why, where

The full stack, one entry per technology: what it does here, why it was
chosen over alternatives, and where to look in the code. Deeper design
rationale lives in [tech-stack.md](../project/tech-stack.md); concept-level
explanations in [theory/](../theory/README.md).

## Backend

### Python 3.12 + uv + ruff + pyright
The toolchain. **uv** manages the venv and lockfile (`uv sync`, `uv run` —
fast, reproducible), **ruff** lints and formats (line length 100), **pyright**
type-checks strictly. CI runs exactly these plus pytest.
*Where:* [pyproject.toml](../../pyproject.toml), [.github/workflows/ci.yml](../../.github/workflows/ci.yml).

### FastAPI + uvicorn
The web framework and ASGI server: one process serves the WebSocket chat
(`/chat`), the REST API (`/api/*`), Prometheus metrics (`/metrics`), and the
built Vue UI (static files at `/`). Chosen for first-class async +
WebSockets + pydantic integration + auto OpenAPI (`/docs`).
`python-multipart` rides along as a direct dependency because Starlette needs
it to parse the file uploads `POST /api/documents` accepts — without it that
endpoint fails at import time rather than at request time.
*Where:* [main.py](../../src/assistant/main.py) (app factory + lifespan wiring),
[api/ws.py](../../src/assistant/api/ws.py), [api/routes.py](../../src/assistant/api/routes.py).

### Pydantic v2 + pydantic-settings
Every boundary is typed: WS frames, config, session records. Settings load
from `.env`/environment with the `ASSISTANT_` prefix; secrets are `SecretStr`
so they can't leak into logs.
*Where:* [config.py](../../src/assistant/config.py), [api/schemas.py](../../src/assistant/api/schemas.py),
[agent/base.py](../../src/assistant/agent/base.py).

### openai SDK (the *client*, not the provider)
One `AsyncOpenAI` client speaks to **every** hosted provider — OpenAI,
Ollama, Gemini — because they all expose the OpenAI-compatible chat API; the
provider is just a `base_url` + key. Streaming, tool calls, and usage
reporting ride the same code path everywhere.
*Where:* [llm/client.py](../../src/assistant/llm/client.py) (`PROVIDER_BASE_URLS`).

### Redis (redis-py asyncio) / fakeredis
Session transcripts, the rolling summary, and the per-turn audit trail — all
keyed by `session_id` with a 24 h TTL. `ASSISTANT_REDIS_URL=fakeredis://`
swaps in an in-memory clone for zero-infra dev; tests always use it.
*Where:* [memory/session.py](../../src/assistant/memory/session.py).

### Qdrant (+ qdrant-client)
The vector database: one collection (`docs`) holding **named vectors** — a
512-dim dense vector and a sparse lexical vector per chunk — enabling hybrid
search with server-side RRF fusion. In tests the same client runs fully
in-memory (`:memory:`). Web UI at `localhost:6333/dashboard`.
*Where:* [rag/store.py](../../src/assistant/rag/store.py); chapter 05.

### MCP SDK (`mcp`)
The Model Context Protocol client and server. The app spawns two bundled
stdio servers (code search, GitHub mock) and adapts their tools into the
shared registry; any third-party MCP server plugs in via config JSON.
*Where:* [mcp/registry.py](../../src/assistant/mcp/registry.py),
[mcp_servers/](../../src/assistant/mcp_servers/); chapter 06.

### Agent frameworks: Pydantic AI + LangGraph (+ the custom loop)
Three implementations of one small `AgentBackend` protocol, switchable per
session — built to *compare* frameworks against a hand-written loop with
identical behavior. The custom loop is the reference; pydantic-ai brings its
own model layer and typed tools; langgraph models the loop as a state graph.
*Where:* [agent/backends/](../../src/assistant/agent/backends/);
measured comparison: [backend comparison](../reference/backend-comparison.md).

## Observability

### structlog
Structured logging: pretty console in dev, JSON lines with
`ASSISTANT_LOG_JSON=true`. One pipeline also reformats stdlib/uvicorn logs,
and `structlog.contextvars` injects `session_id`/`turn_id`/`backend` into
**every** line automatically.
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
(scrapes every 5 s) and Grafana with a pre-provisioned dashboard.
*Where:* [telemetry.py](../../src/assistant/telemetry.py),
[observability/](../../observability/).

### Jaeger (all-in-one)
Local trace UI — zero accounts. Receives OTLP on :4318, UI on :16686.
*Where:* [docker-compose.yml](../../docker-compose.yml) `observability` profile.

## Frontend

### Vue 3 + Pinia + Vite + TypeScript
Composition-API SPA: a Pinia store owns the WebSocket (via `@vueuse/core`'s
`useWebSocket`, auto-reconnect with the same session), typed mirrors of the
WS protocol, markdown rendering (markdown-it, HTML escaping on), tool cards,
per-turn stats line, the "details" audit timeline, and the deep-health dot.
*Where:* [frontend/src/](../../frontend/src/) — start at
[stores/chat.ts](../../frontend/src/stores/chat.ts).

## Testing & quality

### pytest + pytest-asyncio + fakeredis + in-memory Qdrant
344 deterministic tests, no network, no Docker: the WS suite runs across all
three backends via parametrized fixtures; provider quirks (429s, a provider's
`tool_use_failed`, leaked tool syntax) are reproduced with scripted fakes.
*Where:* [tests/](../../tests/); chapter 09.

### GitHub Actions
Two workflows on every push. **CI**: ruff → format check → pyright →
pytest with a coverage floor, across Python 3.12 **and** 3.13, plus a
frontend job (typecheck → vitest → build) and a Docker image build.
**Security**: CodeQL, `pip-audit` and `npm audit`, weekly as well as on push.

## Docker Compose profiles

| Command | Services |
|---|---|
| `docker compose up -d` | redis, qdrant (dev default) |
| `docker compose --profile observability up -d` | + jaeger, prometheus, grafana |
| `docker compose --profile app up --build` | + api (everything in containers) |
