# 10 — Infrastructure & the supporting stack

Short, per technology: what it is, why it's here, where it lives. This is
the "modern Python stack" half of the project's mandate.

## FastAPI + asyncio + uvicorn

**What:** the async Python web framework; uvicorn is the server running it.
**Why async matters here:** an agent turn is almost entirely *waiting* — on
the LLM API, Qdrant, Redis, MCP subprocesses. With `async`/`await`, one
process interleaves thousands of such waits instead of blocking a thread per
chat. WebSockets (long-lived connections) make async practically mandatory.
**Where:** [`main.py`](../src/assistant/main.py) — note the `create_app()`
factory (tests inject fakes) and the lifespan block (connect/cleanup of
Redis, Qdrant, MCP).

## Pydantic & pydantic-settings

**What:** data validation from type annotations; the backbone of FastAPI.
**Why:** every boundary is a typed model — WS frames, chunks, settings — so
malformed data fails loudly at the edge, not deep inside.
**Where:** [`config.py`](../src/assistant/config.py) — one `Settings` class,
every knob an `ASSISTANT_*` env var, validated at startup. "Config is a
typed object, not scattered `os.environ` reads."

## Redis

**What:** in-memory data store.
**Why three jobs, one service:** session history (lists + TTL), the rolling
summary (chapter 07), and the taskiq job broker. It's the only stateful
service besides Qdrant — and both are externalized so API pods stay
stateless (the scaling story, chapter 08).
**Dev trick:** `ASSISTANT_REDIS_URL=fakeredis://` swaps in an in-process
fake — the whole app runs with zero infrastructure.

## Qdrant

Covered in chapter 02 — the vector database. Runs as one container with a
volume; the ingested collection survives restarts.

## taskiq (background jobs)

**What:** an async-native task queue (Redis broker) — think "Celery, but
built for asyncio and actively maintained"; that's exactly why it beat
Celery/arq/Dramatiq in our decision table.
**Why:** work that shouldn't block a request: re-indexing the docs corpus.
`POST /api/reindex` (or the UI button) enqueues; a worker executes; a
scheduler fires the same task nightly at 03:00 (cron label).
**Where:** [`worker.py`](../src/assistant/worker.py); graceful zero-infra
fallback — on `fakeredis://` the reindex runs inline instead of queuing.

## Docker & compose profiles

**What:** containers; compose orchestrates them locally.
**Why profiles:** `docker compose up -d` starts only infra (redis+qdrant) —
the dev loop keeps hot-reload on the host. `--profile app` adds api, worker,
scheduler — the whole platform containerized from one multi-stage
[`Dockerfile`](../Dockerfile) (Node builds the Vue SPA → uv-based Python
runtime serves it).

## uv, ruff, pyright, pytest, CI

The modern Python toolchain, each replacing an older pile:

- **uv** — package/env manager (replaces pip+venv+poetry; lockfile,
  10–100× faster).
- **ruff** — linter *and* formatter in one (replaces flake8+isort+black).
- **pyright** — static type checker; with Pydantic models end-to-end, whole
  bug classes die before runtime.
- **pytest** (+asyncio) — 72 deterministic tests (chapter 09).
- **GitHub Actions** — every push: ruff → format check → pyright → pytest.
- **pre-commit** — the same checks locally before each commit.

## Auth

**What:** optional static bearer token (`ASSISTANT_AUTH_TOKEN`).
**How:** unset = open (zero-config dev). Set: `/api/*` requires
`Authorization: Bearer …`; the chat WS requires `?token=` (browsers can't
set WS headers); wrong token → policy-violation close. The UI captures the
token once from the URL and persists it.
**The defense line:** a bench project gets a demo-grade shared secret *by
design*; the production path is OIDC/SSO at the gateway — stated in config
comments and README, which shows the boundary was considered, not missed.

## Vue 3 frontend (brief — it has its own stack)

Vite + TypeScript + Pinia; the store owns the WebSocket
([`frontend/src/stores/chat.ts`](../frontend/src/stores/chat.ts)); the
protocol types mirror the backend's Pydantic models; tool calls render as
cards; markdown renders with `html: false` (model output is never injected
as raw HTML — the XSS answer).

## Questions you might get

**"Why not Kubernetes?"** — Scope discipline: compose demonstrates the full
multi-service shape (api/worker/scheduler/infra); k8s adds operational
ceremony that teaches nothing extra at bench scale. The containers are
k8s-ready when someone wants them.

**"Why uv over poetry/pip?"** — Speed (seconds, not minutes), one tool for
envs+deps+Python versions, a real lockfile, and it's where the ecosystem
has converged. Same argument shape for ruff and pyright: consolidation and
speed.

**"What's actually stateful?"** — Redis (sessions/summaries/queue) and
Qdrant (vectors), both volume-backed containers. Everything else — API,
worker, scheduler — is stateless and horizontally scalable.
