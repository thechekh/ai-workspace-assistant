# Technology Specification — AI Workspace Assistant Platform

Modern Python stack for the bench project. For each area: the chosen technology, the alternatives that were considered, and why the choice is the best fit. Decisions already agreed are marked ✅; open experiments are marked 🧪.

---

## Guiding principles

1. **Async-native end to end** — FastAPI + asyncio; every I/O-bound dependency (LLM, Qdrant, Redis, jobs) has a first-class async client.
2. **Provider-agnostic LLM layer** — the model is a config value, not a code decision. Dev runs on free tiers, "real" runs on paid keys, switching is a `.env` change.
3. **Typed everywhere** — Pydantic models on the wire, pyright in CI, typed agent tools.
4. **Compare, don't guess** — agent frameworks and embedding models are implemented behind common interfaces so they can be benchmarked against each other. This comparison is itself workshop material.
5. **Observability from day one** — every agent step, tool call, and token is traced.

---

## Decision summary

| Area | Chosen ✅ | Alternatives considered | Why this one |
|---|---|---|---|
| Package/project manager | **uv** | poetry, pip + venv, pdm | 10–100× faster, lockfile, manages Python versions too; the current community default |
| Lint / format | **ruff** | black + flake8 + isort | One tool replaces three, instant, same defaults |
| Type checking | **pyright** | mypy | Faster, better inference, same engine as VS Code |
| Web framework | **FastAPI + uvicorn** | Litestar, granian | Spec'd by the task; uvicorn[standard] gives uvloop + websockets |
| Config | **pydantic-settings** | dynaconf, environs, raw os.environ | Typed settings, `.env` support, validation at startup, same ecosystem as FastAPI |
| Agent runtime | **Custom loop → Pydantic AI → LangGraph** (phased, all three) | pick one framework | The comparison is the learning goal; see §Agent runtime |
| LLM (dev) | **OpenAI free tier** (+ Ollama local fallback) | Gemini free tier, OpenRouter `:free`, xAI Grok credits | Free, fast, OpenAI-compatible API, models with solid tool calling |
| LLM (paid testing) | **OpenAI mini-tier model** (existing $25 budget) | — | Mini models cost cents per million tokens; $25 covers the whole project |
| Embeddings | **text-embedding-3-small → compare voyage-3** 🧪 | BGE-M3 (local), jina | Cheap start, then measured comparison on a golden set; see §Embeddings |
| Vector DB | **Qdrant** | Weaviate, Chroma, pgvector, LanceDB | Fast, great payload filters, native hybrid search, single container |
| Short-term memory | **Redis** + conversation summarization | in-process dicts, Postgres | Spec'd; survives restarts, TTLs for sessions, doubles as job broker |
| Background jobs | **taskiq** (+ taskiq-redis, taskiq-fastapi) | arq, Celery, Dramatiq, RQ | Async-native and actively maintained; see §Background jobs |
| Observability | **Logfire + Langfuse combined** via OpenTelemetry | pick one | Both are OTel-based so they compose; see §Observability |
| Testing | **pytest + pytest-asyncio + httpx + respx** + golden RAG evals | — | Standard modern stack |
| Frontend | **Vue 3 + Vite + TypeScript** | single-file HTML, Streamlit, React | User preference; real SPA experience with WS streaming |
| Containerization | **Docker Compose** | k8s (overkill) | One command brings up app, worker, Qdrant, Redis, frontend |

---

## Language & tooling

- **Python 3.12+** (3.13 if all deps are green).
- **uv** for everything: `uv init`, `uv add`, `uv run`, `uv lock`. No pip, no poetry.
- **ruff** for lint **and** format (`ruff check --fix`, `ruff format`).
- **pyright** in strict-ish mode for `src/`.
- **pre-commit** hooks: ruff check, ruff format, pyright (optional, can be CI-only), `uv lock --check`.
- **GitHub Actions** CI: lint → typecheck → tests.

---

## Backend framework

**FastAPI + uvicorn[standard] + Pydantic v2 + pydantic-settings.** ✅

- `uvicorn[standard]` pulls in uvloop, httptools, and the `websockets` library — everything the WS chat endpoint needs.
- All WS frames are typed Pydantic models (discriminated union on `type`): `user_message`, `token`, `tool_call`, `tool_result`, `final`, `error`.
- Configuration is a single `Settings` class validated at startup:

```python
from typing import Literal
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ASSISTANT_")

    # LLM — provider is a config value, not a code decision
    llm_provider: Literal["openai", "ollama", "gemini", "openai"] = "openai"
    llm_model: str = "gpt-4.1-nano"
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None  # for OpenAI-compatible endpoints

    # Agent backend — switchable for comparison
    agent_backend: Literal["custom", "pydantic_ai", "langgraph"] = "custom"

    # Embeddings
    embedding_provider: Literal["openai", "voyage"] = "openai"
    embedding_model: str = "text-embedding-3-small"

    # Infra
    qdrant_url: str = "http://localhost:6333"
    redis_url: str = "redis://localhost:6379/0"

    # Observability
    logfire_token: SecretStr | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
```

*Alternatives:* Litestar is a good framework but the task spec names FastAPI; granian can replace uvicorn later without code changes if we ever need more raw throughput.

---

## Agent runtime — three implementations, one interface

**Decision: implement all three, in phases, behind a common protocol — selected at runtime by `ASSISTANT_AGENT_BACKEND`.** ✅

Order: **custom loop → Pydantic AI → LangGraph.**

### Why not separate git branches?

Branches work for a quick spike, but for a lasting comparison they have real downsides: the shared code (WS server, RAG, MCP registry, memory) drifts across branches, you can't demo two backends side by side in the same running app, and every improvement must be merged three times. Instead:

```
src/assistant/agent/
├── base.py           # AgentBackend protocol: async def run(session, message) -> AsyncIterator[AgentEvent]
├── tools/          # shared tool definitions (RAG search, MCP tools)
└── backends/
    ├── custom.py     # Phase A — hand-written ReAct loop
    ├── pydantic_ai.py# Phase B
    └── langgraph.py  # Phase C
```

All three backends receive the same tool set and emit the same `AgentEvent` stream, so the WS layer and frontend don't care which one is active. Switching = one env var (or even a per-session query param — great for the live demo: same question, three runtimes). Use short-lived feature branches per phase for development (`feat/agent-pydantic-ai`), merged to `main` when done — but the *comparison* lives in `main` as three co-existing modules, not three diverged branches.

### The three phases

| Phase | Backend | What it teaches / shows |
|---|---|---|
| A | **Custom loop** (~50–100 lines) | The raw mechanics: messages array, tool-call detection, executing tools, feeding results back, loop-until-final-answer. No magic. |
| B | **Pydantic AI** | The modern typed framework: tools as decorated functions with validated args, built-in provider abstraction (OpenAI/Gemini/Ollama by model string), native MCP client support, native Logfire instrumentation, streaming. |
| C | **LangGraph** | The industry graph approach: explicit state machine, checkpointing (resume a conversation mid-graph), human-in-the-loop interrupts, the ecosystem employers name-drop. |

### Comparison criteria (workshop slide)

Lines of code · streaming support · effort to attach MCP tools · memory/checkpoint story · observability integration · testability · how hard it is to debug when the model misbehaves.

*Avoided:* plain LangChain agents (legacy API surface, superseded by LangGraph), CrewAI/AutoGen (multi-agent focus — out of scope).

---

## LLM providers

**Strategy: everything speaks the OpenAI-compatible chat API (or goes through Pydantic AI's model abstraction), so the provider is pure config.** ✅

Note on naming — two different things sound alike:

- **OpenAI** (openai.com) — an *inference provider* running open models (Llama 3.3 70B, Llama 3.1 8B, Qwen…) on custom hardware. Generous **free tier**, very fast, OpenAI-compatible endpoint. This is the recommended free option.
- **Grok** (x.ai) — xAI's own model family. Has offered periodic free API credits; check current terms if interested. Not needed for this project.

### Dev / free tier (start here)

| Provider | Cost | Notes |
|---|---|---|
| **OpenAI** ✅ | Free tier (rate-limited) | `gpt-4.1-nano` has solid tool calling — good enough to develop the whole agent loop |
| **Ollama** (local) | Free, unlimited | Runs `llama3.2` / `qwen2.5` locally; perfect for offline dev and tests. Caveat: small local models are noticeably weaker at tool calling — fine for plumbing, not for judging agent quality |
| Google Gemini (AI Studio) | Generous free tier | `gemini-*-flash` models; good tool calling; second option if OpenAI limits bite |
| OpenRouter | Free `:free` model variants | One API over many providers; handy for quick model comparisons |

### Paid testing (existing $25 OpenAI budget)

Use a **mini-tier model** (e.g. `gpt-4o-mini` / `gpt-5-mini` class) — they cost cents per million tokens, so $25 realistically covers all development and the demo. Reserve it for: final quality testing, the workshop demo, and the embedding comparison (see below). Set a **hard spend limit** in the OpenAI dashboard.

### Later / production story

The description names "OpenAI / Claude" — in a production version the same config switch adds Anthropic Claude (strong tool-use/agent behavior); the provider-agnostic layer means no rewrite. Mention this in the workshop as the scaling path.

---

## Embeddings

**Start: OpenAI `text-embedding-3-small`. Then: measured comparison against Voyage `voyage-3`.** ✅🧪

- `text-embedding-3-small` — ~$0.02 per million tokens; embedding the whole doc corpus costs effectively nothing out of the $25 budget.
- **Voyage AI** (`voyage-3`, or `voyage-code-3` if the corpus is code-heavy) — consistently top-ranked on retrieval benchmarks; has a free token allowance that should cover the comparison corpus.

### Comparison methodology (do this properly — it's a workshop highlight)

1. Embeddings from different models are **not comparable vectors** — the corpus must be embedded once per model into **separate Qdrant collections** (or one collection with named vectors), e.g. `docs_openai_small`, `docs_voyage3`.
2. Build a **golden question set** (~20–30 real engineer questions with known "correct" source chunks).
3. Measure per model: **recall@k** and **MRR** on retrieval, plus end-to-end answer quality (LLM-as-judge or manual grading).
4. Publish the table in the workshop; keep whichever wins as the default in config.

*Alternative kept in reserve:* **BGE-M3** via sentence-transformers — free, local, multilingual, supports dense+sparse; the right answer if the platform must ever run fully on-prem.

---

## Vector DB & retrieval

**Qdrant.** ✅

- Single container, async Python client (`qdrant-client`), great payload filtering (filter by `doc_type`, `team`, `path`), snapshots for backup.
- **Roadmap upgrade:** hybrid search — dense vectors + sparse (BM25-style) fused with RRF via Qdrant's Query API — plus a **reranker** (Voyage `rerank-2`, Cohere, or a local cross-encoder like `bge-reranker`) over the top-20 candidates. Hybrid + rerank is the current standard for serious RAG and a visible quality jump for the demo.
- Ingestion parsing via **docling** (PDF/HTML/DOCX → clean Markdown), heading-aware chunking (~400–800 tokens with overlap), metadata attached to every chunk.

*Alternatives:* pgvector (fine if we wanted "just Postgres", but we have no other Postgres need); Chroma (prototyping only); Weaviate (heavier, no advantage here); LanceDB (nice embedded option, smaller ecosystem).

---

## Memory

**Short-term: Redis. Long-term: Qdrant. Plus conversation summarization.** ✅

- Each WS session has a Redis-backed history (`LIST`/`JSON` per session id, TTL for abandoned sessions).
- **Summarization strategy (standard trick, good workshop material):** when history exceeds a token budget (e.g. ~4k tokens), summarize the *oldest* turns with the cheap model into a rolling summary block; keep the most recent N turns verbatim. Prompt context becomes: `system + rolling_summary + recent_turns`. This bounds cost and latency while preserving continuity.
- Long-term memory (optional, phase 2+): distilled facts ("user works on billing-service") embedded into a dedicated Qdrant collection and retrieved like RAG.

---

## Background jobs

**taskiq + taskiq-redis + taskiq-fastapi.** ✅ (Celery explicitly rejected.)

Why taskiq over arq (both were candidates):

| | taskiq ✅ | arq |
|---|---|---|
| Maintenance | Actively developed | Minimal-maintenance mode |
| FastAPI integration | `taskiq-fastapi` shares DI and settings with the app | manual |
| Broker | Redis (already in our stack) via `taskiq-redis` | Redis |
| Scheduler | Built-in (`TaskiqScheduler`) — e.g. nightly re-index | Built-in cron |
| Feel | Modern, typed, async-first | Simpler but smaller |

Used for: document ingestion/re-indexing (parse → chunk → embed → upsert), scheduled corpus refresh, and the embedding-comparison batch runs. (For day one, ingestion also works as a plain `uv run python -m assistant.rag.ingest` CLI — taskiq arrives when we want the "platform" shape.)

*Alternatives rejected:* Celery (heavy, sync-first, poor asyncio story), Dramatiq/RQ (solid but sync-first — wrong paradigm for this codebase).

---

## Observability

**Logfire and Langfuse, combined — yes, this works, because both are OpenTelemetry-based.** ✅

Architecture: **Logfire SDK is the single instrumentation layer; its spans are exported to both backends.**

```python
import logfire

logfire.configure()  # sends to Logfire cloud
logfire.instrument_fastapi(app)  # HTTP + WS spans
logfire.instrument_httpx()  # outgoing LLM/API calls
logfire.instrument_pydantic_ai()  # agent runs, tool calls, tokens

# Additionally forward the same OTel spans to Langfuse's OTLP endpoint
# (BatchSpanProcessor + OTLPSpanExporter pointed at Langfuse, authed with its keys)
```

Division of labor:

- **Logfire** → application view: request latency, WS lifecycle, Redis/Qdrant calls, exceptions, plus one-line Pydantic AI instrumentation. Free tier is enough.
- **Langfuse** → LLM view: generation traces with token costs per model, prompt comparisons, **scores/evals** (attach the golden-set results), session replays. Use **Langfuse Cloud free tier** for the bench project — self-hosting v3 needs Postgres + ClickHouse + Redis + S3, which is more infrastructure than the project deserves (mention self-hosting as the "internal platform" production path in the workshop).

Honest caveat: there is overlap, and two dashboards is a cost in attention. If it ever feels like too much, Logfire alone is sufficient for the demo — but showing "one OTel instrumentation, two specialized backends" is itself a strong modern-stack talking point.

---

## Testing & evals

✅ Agreed as proposed:

- **pytest + pytest-asyncio** — async tests throughout.
- **httpx.AsyncClient / TestClient** — WS endpoint tests (connect, send, assert typed frames).
- **respx** — mock LLM HTTP calls; agents are tested with scripted model responses (tool-call → result → final) so tests are fast, free, and deterministic.
- **Golden-question eval set** (~20–30 Q/A pairs with expected source chunks) — reused for: RAG regression testing, the embedding comparison, and the agent-backend comparison. Run in CI on a small subset; full run manually/nightly.

---

## Frontend

**Vue 3 + Vite + TypeScript SPA.** ✅ (User preference — replaces the "single HTML file" idea.)

| Piece | Choice |
|---|---|
| Framework | Vue 3, Composition API, `<script setup lang="ts">` |
| Build | Vite (dev server + proxy to backend) |
| WS client | `useWebSocket` from **@vueuse/core** — auto-reconnect and heartbeat for free |
| State | **Pinia** (sessions, message list, streaming buffer) |
| Rendering | `markdown-it` + `highlight.js`/shiki for streamed Markdown & code blocks |
| Styling | Tailwind CSS (optional but fast for a chat UI) |

Layout & wiring:

```
frontend/            # Vue app (own package.json)
src/assistant/       # Python backend
```

- **Dev:** Vite on `:5173` with proxy `/chat` (ws: true) and `/api` → `:8000`. Two processes, hot reload on both sides.
- **Demo/prod:** `npm run build` → FastAPI serves `frontend/dist` via `StaticFiles` (or an nginx container) — single URL for the demo.
- UI features worth building: streaming token render, visible **tool-call cards** ("🔧 search_code → 12 results"), agent-backend switcher dropdown (custom / pydantic-ai / langgraph) — directly showcases the comparison.

---

## Infrastructure

**Docker Compose** services:

| Service | Image / build | Purpose |
|---|---|---|
| `api` | project Dockerfile (uv-based) | FastAPI + uvicorn |
| `worker` | same image, taskiq entrypoint | ingestion & scheduled jobs |
| `qdrant` | `qdrant/qdrant` | vector DB (volume-persisted) |
| `redis` | `redis:7-alpine` | memory + task broker |
| `frontend` | node build stage → nginx (or served by `api`) | Vue SPA |

MCP servers (GitHub, custom code-search via FastMCP) run either as sidecar containers or are spawned by the app, depending on transport (stdio vs streamable HTTP).

---

## Phased roadmap

| Phase | Deliverable |
|---|---|
| 0 | Scaffolding: uv project, ruff/pyright/pre-commit, CI, compose (qdrant+redis), `Settings` |
| 1 | WS chat endpoint + Redis sessions + streaming with **OpenAI free tier**; minimal Vue chat page |
| 2 | RAG: docling ingestion CLI → Qdrant; `search_docs` tool; golden question set v1 |
| 3 | **Custom agent loop** (backend A) with RAG + first tools; tool-call cards in UI |
| 4 | MCP: registry + official GitHub server + custom FastMCP `search_code` server |
| 5 | **Pydantic AI backend** (B); Logfire instrumentation; Langfuse OTel forwarding |
| 6 | **LangGraph backend** (C); backend comparison writeup; conversation summarization |
| 7 | Embedding comparison (openai vs voyage-3) with published metrics; hybrid search + reranker |
| 8 | Polish: taskiq scheduled re-index, Vue UI polish, workshop slides & live-demo script |

---

## Final stack, one line each

Python 3.12 · uv · ruff · pyright · FastAPI · uvicorn · Pydantic v2 · pydantic-settings · WebSockets · custom-loop / Pydantic AI / LangGraph (comparative) · OpenAI free tier → OpenAI mini ($25) · text-embedding-3-small → voyage-3 (comparative) · Qdrant (+hybrid+rerank) · Redis (+summarization) · taskiq · MCP (FastMCP + official servers) · Logfire + Langfuse over OpenTelemetry · pytest/respx + golden evals · Vue 3 + Vite + TS · Docker Compose.
