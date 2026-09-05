# 10 — Infrastructure & the supporting stack

**What this chapter answers: what each supporting technology in this stack
actually is, why it was picked over its named alternatives, and where it
lives in the repository.** It does not cover what Redis and Qdrant *store* —
that's [07 — Conversation memory](07-memory.md) and
[02 — Embeddings & vector search](02-embeddings-and-vector-search.md); this
chapter is the supporting cast around them.

This is the "modern Python stack" half of the project's mandate: short, per
technology, what it is, why it's here, where it lives.

## 1. FastAPI + asyncio + uvicorn

**What:** the async Python web framework; uvicorn is the server running it.
**Why async matters here:** an agent turn is almost entirely *waiting* — on
the LLM API, Qdrant, Redis, MCP subprocesses. With `async`/`await`, one
process interleaves thousands of such waits instead of blocking a thread per
chat. WebSockets (long-lived connections) make async practically mandatory.
**Where:** [`main.py`](../../src/assistant/main.py) — `create_app()` is the
factory (tests pass it fakes for Redis, the LLM and the retriever); it wires
a `lifespan` block that calls `build_runtime()` on startup, returning a
`Runtime` dataclass that owns teardown via `Runtime.aclose()`, and tears
down the Redis/Qdrant/MCP connections on shutdown. The module also defines a
lazy `__getattr__` for `app` itself — the reason, per
[CLAUDE.md](../../CLAUDE.md), that importing `assistant.main` has to stay
side-effect free: a plain import must never read a developer's `.env`.

## 2. Pydantic & pydantic-settings

**What:** data validation from type annotations; the backbone of FastAPI.
**Why:** every boundary is a typed model — WS frames, chunks, settings — so
malformed data fails loudly at the edge, not deep inside.
**Where:** [`config.py`](../../src/assistant/config.py) — one `Settings` class,
every knob an `ASSISTANT_*` env var, validated at startup. "Config is a
typed object, not scattered `os.environ` reads."

## 3. Redis

**What:** in-memory data store.
**Why two jobs, one service:** session history (lists + TTL) and the rolling
summary (chapter 07). It's the only stateful service besides Qdrant — and
both are externalized so API pods stay stateless (the scaling story,
chapter 08).
**Dev trick:** `ASSISTANT_REDIS_URL=fakeredis://` swaps in an in-process
fake — the whole app runs with zero infrastructure.

## 4. Qdrant

Covered in chapter 02 — the vector database. Runs as one container with a
volume; the ingested collection survives restarts.

## 5. Docker & compose profiles

**What:** containers; [`docker-compose.yml`](../../docker-compose.yml)
orchestrates them locally, gated behind profiles so a plain `up` stays
minimal:

| Profile | Command | Starts |
|---|---|---|
| *(default)* | `docker compose up -d` | `redis` + `qdrant` only — the dev loop keeps hot-reload on the host |
| `app` | `docker compose --profile app up` | + `api`, from one multi-stage [`Dockerfile`](../../Dockerfile) |
| `observability` | `docker compose --profile observability up` | + Jaeger (`:16686`), Prometheus (`:9090`), Grafana (`:3000`) — see [09](09-observability-and-evals.md) |

A worked example of what "one multi-stage Dockerfile" means concretely:
[`Dockerfile`](../../Dockerfile) builds the Vue frontend in a `node:22-alpine`
stage, then copies only the built `frontend/dist` into a
`ghcr.io/astral-sh/uv:python3.13-bookworm-slim` runtime stage that never sees
`node_modules`. Dependencies are synced with `uv sync --frozen --no-dev
--no-install-project` *before* `src/` is even copied in, so the Docker layer
cache survives every code change that doesn't touch `pyproject.toml`; the
final image drops root (`useradd --uid 10001 app`) before `CMD` ever runs.

## 6. uv, ruff, pyright, pytest, CI

The modern Python toolchain, each replacing an older pile — the alternatives
and the full reasoning are recorded in
[project/tech-stack.md](../project/tech-stack.md#decision-summary):

| Tool | Replaces | Why this one |
|---|---|---|
| **uv** | pip + venv + poetry | 10–100× faster; one tool for envs, deps and Python versions; a real lockfile |
| **ruff** | flake8 + isort + black | Lint *and* format in one, instant |
| **pyright** | mypy | Faster, better inference, same engine as VS Code |
| **pytest** (+asyncio, +cov) | — | 573 deterministic tests with a coverage floor (2026-09-05, `uv run pytest -q`; chapter 09) |

- **GitHub Actions** — every push runs two workflows. *CI*: ruff → format
  check → pyright → pytest+coverage on Python **3.12 and 3.13** (the image
  ships 3.13), a frontend job (vue-tsc → vitest → build), and a Docker image
  build. *Security*: `pip-audit` and `npm audit`, also weekly — a dependency
  clean at merge time can rot later — plus a CodeQL job that skips itself
  while the repository is private (its upload needs GitHub Advanced
  Security).
- **pre-commit** — the same checks locally before each commit.

## 7. Auth

**What:** optional static bearer token (`ASSISTANT_AUTH_TOKEN`).
**How:** unset = open (zero-config dev). Set: `/api/*` requires
`Authorization: Bearer …`; the chat WS requires `?token=` (browsers can't
set WS headers); wrong token → policy-violation close. The UI captures the
token once from the URL and persists it.
**The defense line:** a bench project gets a demo-grade shared secret *by
design*; the production path is OIDC/SSO at the gateway. That boundary is
tracked openly as deferred work in
[project/future-tools.md](../project/future-tools.md) — a stated trigger
("any second team, or per-user quotas/audit"), not a gap discovered later.

## 8. Vue 3 frontend (brief — it has its own stack)

Vite + TypeScript + Pinia; the store owns the WebSocket
([`frontend/src/stores/chat.ts`](../../frontend/src/stores/chat.ts)); the
protocol types mirror the backend's Pydantic models; tool calls render as
cards; markdown renders with `html: false` (model output is never injected
as raw HTML — the XSS answer).

## 9. Questions you might get

**"Why not Kubernetes?"** — Scope discipline, and it's a named trade-off in
[project/tech-stack.md](../project/tech-stack.md), not an oversight: compose
demonstrates the full multi-service shape (api + infra) while k8s "adds
operational ceremony that teaches nothing extra at bench scale". The
containers are k8s-ready when someone wants them.

**"Why uv over poetry/pip?"** — Speed (seconds, not minutes), one tool for
envs+deps+Python versions, a real lockfile, and it's where the ecosystem
has converged. Same argument shape for ruff and pyright: consolidation and
speed — §6's table has the full replacement list.

**"What's actually stateful?"** — Redis (sessions/summaries) and Qdrant
(vectors), both volume-backed containers. The API is stateless and
horizontally scalable.

**"Where's the background-job layer?"** — Deliberately absent. It existed
(taskiq + a nightly re-index cron) and was removed: documents are embedded
once, at upload, so there was no batch left to schedule and the job was a
no-op in every real configuration. taskiq had already won a real comparison
against arq — actively developed, and `taskiq-fastapi` shared DI with the
app — and that comparison is kept on record in
[project/tech-stack.md](../project/tech-stack.md#background-jobs) rather
than deleted, because keeping a queue only to demonstrate a queue is how
dead weight gets defended.

## 10. Reading it honestly

- **Single instance, no replication.** Redis and Qdrant each run as one
  container with one volume. That's correct for a bench project and wrong
  for anything that needs to survive a host dying — no failover story is
  built or claimed.
- **Grafana runs wide open on purpose, and only justifiably on purpose
  here.** `GF_AUTH_ANONYMOUS_ENABLED=true` with the `Admin` role
  ([`docker-compose.yml`](../../docker-compose.yml)) is fine on localhost and
  a real gap the moment this compose file seeds a shared deployment —
  tracked explicitly as deferred work in
  [project/future-tools.md](../project/future-tools.md), not discovered
  later by someone else.
- **`pyright` runs in standard mode, not strict.** Turning strict on was
  measured, not just assumed or postponed: doing so surfaces 552 errors
  (mostly `reportUnknown*` in tests) — which is exactly why it stayed a
  deliberately scoped future task instead of a quick flag flip
  ([project/future-tools.md](../project/future-tools.md)).
- **A non-root, multi-stage Docker image reduces attack surface; it is not
  a security review.** See [reference/security.md](../reference/security.md)
  for the actual threat model this infrastructure sits inside, and what is
  deliberately not built yet.

## 11. Related

- [07 — Conversation memory](07-memory.md) — what actually lives in Redis
- [02 — Embeddings & vector search](02-embeddings-and-vector-search.md) — what actually lives in Qdrant
- [handbook/03 — Technologies](../handbook/03-technologies.md) — the same stack, from the "how do I run it" side
- [project/tech-stack.md](../project/tech-stack.md) — every technology decision in this chapter, with the alternatives and why they lost
- [reference/security.md](../reference/security.md) — the auth model in §7, threat model and all
