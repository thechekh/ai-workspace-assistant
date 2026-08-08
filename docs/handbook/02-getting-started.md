# 02 — Getting started: every way to run it

## Prerequisites

| Tool | Why | Check |
|---|---|---|
| Python 3.12+ & [uv](https://docs.astral.sh/uv/) | backend deps & venv | `uv --version` |
| Node.js 22 | build the Vue UI once (vite 8 requires ≥20.19/≥22.12; `.nvmrc` and CI pin 22) | `node --version` |
| Docker Desktop | Redis, Qdrant, observability stack (optional — see Mode A) | `docker info` |

One-time setup:

```sh
uv sync                                        # install backend deps into .venv
cd frontend && npm install && npm run build && cd ..   # build the UI (served at /)
cp .env.example .env                           # then edit (see reference below)
```

## The four run modes

Each mode adds infrastructure; the app degrades gracefully when something is
missing (the health dot tells you what's up — chapter 07).

### Mode A — zero infra (no Docker, no API key)

```sh
# .env: ASSISTANT_REDIS_URL=fakeredis://   (and leave provider=fake)
uv run uvicorn assistant.main:app --reload
# open http://localhost:8000/
```

- Sessions live in memory (lost on server restart). Qdrant is absent, so
  `search_docs` returns an error result — the agent still answers (that's the
  crash-isolation design). The **FakeLLM** answers deterministically and
  triggers tools on keywords (PRs → github tool, "search code for X" → code
  tool, URL → fetch_url, "…?" → search_docs). Costs nothing, works offline.

### Mode B — real infrastructure (Docker: Redis + Qdrant)

```sh
docker compose up -d                                   # redis :6379, qdrant :6333
# .env: remove ASSISTANT_REDIS_URL override (default = redis://localhost:6379/0)
uv run python -m assistant.rag.ingest evals/corpus --recreate   # fill the collection
uv run uvicorn assistant.main:app --reload
```

Now RAG is real: docs questions retrieve from Qdrant with citations, sessions
survive restarts, health dot goes green. Browse the vectors at
http://localhost:6333/dashboard.

### Mode C — real model (Groq, free)

```sh
# .env:
#   ASSISTANT_LLM_PROVIDER=groq
#   ASSISTANT_LLM_MODEL=llama-3.3-70b-versatile
#   ASSISTANT_LLM_API_KEY=gsk_...        # free key: console.groq.com
```

Restart the server — that's it. Streaming becomes real inference, the stats
line under each answer shows **real** token counts (no "(est)") and an
indicative cost. Rate limits and model quirks are handled automatically
(chapter 04). Daily budget exhausted? Switch to
`ASSISTANT_LLM_MODEL=llama-3.1-8b-instant` (separate quota).

### Mode D — observability stack (Jaeger + Prometheus + Grafana)

```sh
docker compose --profile observability up -d
# .env: ASSISTANT_OTLP_ENDPOINT=http://localhost:4318
# restart the server
```

Traces appear in Jaeger (:16686), metrics in Prometheus (:9090), the
provisioned dashboard in Grafana (:3000, no login). Chapter 07 is the tour.

### Everything in containers (full platform)

```sh
docker compose --profile app up --build   # api + worker + scheduler + redis + qdrant
```

## Background jobs (optional in dev)

```sh
uv run taskiq worker assistant.worker:broker        # processes queued re-index jobs
uv run taskiq scheduler assistant.worker:scheduler  # fires the nightly cron (03:00)
```

Both only do something when **`ASSISTANT_CORPUS_DIR`** points at a folder;
without it the nightly job is a no-op and the UI's **Re-index** button returns
400, because documents added through `POST /api/documents` live in Qdrant and
need no re-indexing. With a corpus configured and real Redis the button queues
a job (needs the worker running); with `fakeredis://` it re-indexes inline.
CLI alternative: `uv run python -m assistant.rag.ingest <folder> [--recreate]`.

## Frontend development

```sh
cd frontend
npm run dev      # Vite on :5173, hot reload, proxies /chat + /api to :8000
npm run build    # vue-tsc type check + production bundle -> frontend/dist
```

## `.env` reference — every variable

All variables use the `ASSISTANT_` prefix and map 1:1 to
[config.py](../../src/assistant/config.py). Unset = the shown default.

| Variable | Default | Meaning |
|---|---|---|
| `AUTH_TOKEN` | *(unset = open)* | When set: `/api/*` (mutating/sensitive) needs `Authorization: Bearer <t>`, WS needs `?token=`. Open once as `http://localhost:8000/?token=<t>` — the UI persists it |
| `LLM_PROVIDER` | `fake` | `fake` \| `groq` \| `ollama` \| `gemini` \| `openai` — all speak the OpenAI chat API |
| `LLM_MODEL` | `llama-3.3-70b-versatile` | Model name at the provider |
| `LLM_API_KEY` | — | Required for hosted providers (Groq key starts `gsk_`) |
| `LLM_BASE_URL` | *(provider default)* | Override the provider endpoint URL |
| `AGENT_BACKEND` | `custom` | Default runtime: `custom` \| `pydantic_ai` \| `langgraph` (UI can switch per session) |
| `EMBEDDING_PROVIDER` | `hash` | `hash` (offline) \| `openai` \| `voyage` |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Used when provider=openai |
| `EMBEDDING_API_KEY` / `VOYAGE_API_KEY` | — | Keys for the paid embedders |
| `QDRANT_COLLECTION` | `docs` | Collection name |
| `RETRIEVAL_MODE` | `hybrid` | `dense` \| `hybrid` (dense + sparse, RRF fusion) |
| `RERANK_ENABLED` | `true` | Lexical reranker over the top-20 candidates |
| `HISTORY_CHAR_BUDGET` | `8000` | When un-summarized history exceeds this, old turns fold into the rolling summary |
| `HISTORY_KEEP_RECENT` | `6` | Messages always kept verbatim |
| `SESSION_TTL_SECONDS` | `86400` | How long transcript, summary and audit trail live in Redis |
| `SYSTEM_PROMPT` | *(see config.py)* | Steers tool choice and the honesty rules — override to change persona/behaviour |
| `DEBUG` | `true` | Also serves the minimal WS console at `/dev` |
| `CORPUS_DIR` | *(unset)* | Optional folder to (re)ingest from; unset means the knowledge base is filled via `POST /api/documents` |
| `MCP_ENABLED` | `true` | Master switch for MCP tool servers |
| `MCP_SERVERS` | *(two bundled stdio servers)* | JSON list — see `.env.example` for the real-GitHub swap |
| `REDIS_URL` | `redis://localhost:6379/0` | `fakeredis://` = in-memory, zero setup |
| `QDRANT_URL` | `http://localhost:6333` | |
| `LOG_LEVEL` | `INFO` | |
| `LOG_JSON` | `false` | `true` → JSON lines (production shape; pretty console otherwise) |
| `LOG_PROMPTS` | `false` | Dev-only: dump full prompts/completions into logs |
| `OTLP_ENDPOINT` | *(unset = tracing off)* | `http://localhost:4318` → local Jaeger |
| `LOGFIRE_TOKEN` | *(unset)* | Also export spans to Logfire cloud (+ FastAPI/httpx auto-instr.) |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | *(unset)* | Also export spans to Langfuse (LLM view) |

## Verify your setup (2 minutes)

1. `curl localhost:8000/healthz` → `{"status":"ok"}` (process is alive).
2. `curl localhost:8000/api/health` → every component you expect is `"ok"`
   (this is what the UI's header dot polls every 10 s).
3. Open http://localhost:8000/ → send `ping` → tokens stream, a stats line
   appears under the answer.
4. `uv run pytest -q` → `129 passed` (fully offline, ~13 s).

Then work through [the testing checklist](../reference/testing.md) — the feature-by-
feature manual checklist for the mode you're in.
