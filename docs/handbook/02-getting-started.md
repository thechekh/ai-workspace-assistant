# 02 — Getting started: every way to run it

**What this chapter covers: every way to bring the app up, from zero
infrastructure to the full container stack, every `.env` variable, and how
to fill the knowledge base.** It does not explain why any piece of
infrastructure was chosen over an alternative — see
[03 — Every technology](03-technologies.md) for that; this page is about
running it, not justifying it.

## 1. Prerequisites

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

## 2. The four run modes

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
http://localhost:6333/dashboard. The fixture corpus this ingests is exactly
5 files splitting into 30 chunks (measured 2026-09-05 — see
[05 — RAG & Qdrant §2](05-rag-qdrant.md) for the reproduction command).

### Mode C — real model (OpenAI, free)

```sh
# .env:
#   ASSISTANT_LLM_PROVIDER=openai
#   ASSISTANT_LLM_MODEL=gpt-4.1-nano
#   ASSISTANT_LLM_API_KEY=sk-...        # key: platform.openai.com/api-keys
```

Restart the server — that's it. Streaming becomes real inference, the stats
line under each answer shows **real** token counts (no "(est)") and an
indicative cost. Rate limits and model quirks are handled automatically
(chapter 04, which also has the full pricing table and what a demo actually
costs). Daily budget exhausted? Switch to `ASSISTANT_LLM_MODEL=gpt-4o-mini`
(separate quota).

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
docker compose --profile app up --build   # api + redis + qdrant
```

![docker compose ps showing five containers up, and the deep-health JSON with every component ok](../images/localhost-stack.png)

Line by line (captured 2026-09-05,
[reference/localhost.md §5](../reference/localhost.md)):

- **`docker compose ps`** — five containers `Up`, Redis and Qdrant
  `(healthy)`.
- **`"status": "ok"`** — every component `/api/health` checked answered;
  this is what turns the header dot green.
- **`"qdrant": … "points": 93`** — a live count from whatever had been
  ingested at capture time, not the clean 30-chunk fixture corpus above.
- **`"mcp": … "servers_connected": "2/2"`** and eleven tool names — this
  particular capture had the *production* GitHub MCP server configured
  (chapter 06); the bundled dev default here is 5 MCP tools (the `code`
  server plus the mocked `github` one), and `/api/health` would list those
  names instead.

## 3. Filling the knowledge base

It starts empty and stays empty until you put something in it. Nothing
pre-loads it, and nothing re-indexes on a schedule — a document is embedded
once, when it is uploaded, so there is no batch job to run (the taskiq
scheduler that once did this was removed in full — chapter 03 §6 has the
reasoning).

Four ways in:

```sh
# 1. ask the assistant, in chat:
#      "ingest the docs from thechekh/demo-payments-platform"
#      (add "including the code" and source files are indexed too)
#    -> the agent calls its ingest_repo tool; the next question answers from them
# 2. the UI's Documents panel  (drag in .md / .txt / .rst, or paste text)
# 3. over HTTP
curl -F "files=@architecture.md" localhost:8000/api/documents
# 4. a folder, one-off from the CLI
uv run python -m assistant.rag.ingest <folder> [--recreate]
```

`ingest_repo` names every source `owner/repo/path`, so two repositories can
never overwrite each other's `README.md`. Public repos work with no token;
set `ASSISTANT_GITHUB_TOKEN` (fine-grained, read-only) for private ones. It
is the agent's **only** write capability — additive, and pinned by a test.

Worked example, measured 2026-09-05, entirely offline (chunking needs no
Qdrant): `uv run python -c "from pathlib import Path; from assistant.rag.ingest import load_chunks; print(len(load_chunks(Path('evals/corpus'))))"`
prints `30` — the 5 Markdown files under
[evals/corpus/](../../evals/corpus/) are what ingestion path 4 above would
load, and the number [05 — RAG & Qdrant](05-rag-qdrant.md) evaluates against.

## 4. Frontend development

```sh
cd frontend
npm run dev      # Vite on :5173, hot reload, proxies /chat + /api to :8000
npm run build    # vue-tsc type check + production bundle -> frontend/dist
npm run lint && npm run typecheck && npm run test:run   # what CI runs on the frontend job
```

## 5. `.env` reference — every variable

All variables use the `ASSISTANT_` prefix and map 1:1 to
[config.py](../../src/assistant/config.py). Unset = the shown default.

| Variable | Default | Meaning |
|---|---|---|
| `AUTH_TOKEN` | *(unset = open)* | When set: `/api/*` (mutating/sensitive) needs `Authorization: Bearer <t>`, WS needs `?token=`. Open once as `http://localhost:8000/?token=<t>` — the UI persists it |
| `LLM_PROVIDER` | `fake` | `fake` \| `openai` \| `ollama` \| `gemini` — all speak the OpenAI chat API |
| `LLM_MODEL` | `gpt-4.1-nano` | Model name at the provider |
| `LLM_API_KEY` | — | Required for hosted providers (OpenAI key starts `sk-...`) |
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
| `RATE_LIMIT_ENABLED` | `true` | Master switch for both limiters below |
| `RATE_LIMIT_TURNS_PER_MINUTE` | `20` | Chat turns per session; `0` disables just this bucket |
| `RATE_LIMIT_UPLOADS_PER_HOUR` | `50` | Indexing requests per caller (`POST /api/documents`) |
| `SYSTEM_PROMPT` | *(see config.py)* | Steers tool choice and the honesty rules — override to change persona/behaviour |
| `DEBUG` | `true` | Also serves the minimal WS console at `/dev` |
| `GITHUB_TOKEN` | *(unset)* | For the `ingest_repo` tool: unset = public repos only (60 req/h); a read-only PAT unlocks private repos |
| `MCP_ENABLED` | `true` | Master switch for MCP tool servers |
| `MCP_SERVERS` | *(two bundled stdio servers)* | JSON list — see `.env.example` for the real-GitHub swap |
| `REDIS_URL` | `redis://localhost:6379/0` | `fakeredis://` = in-memory, zero setup |
| `QDRANT_URL` | `http://localhost:6333` | |
| `LOG_LEVEL` | `INFO` | |
| `LOG_JSON` | `false` | `true` → JSON lines (production shape; pretty console otherwise) |
| `LOG_PROMPTS` | `false` | Dev-only: dump full prompts/completions into logs |
| `OTLP_ENDPOINT` | *(unset = tracing off)* | `http://localhost:4318` → local Jaeger |
| `LOGFIRE_TOKEN` | *(unset)* | Also export spans to Logfire cloud (+ FastAPI/httpx auto-instr.) |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | *(unset)* | Also export spans to Langfuse (LLM view) |
| `LANGFUSE_HOST` | `https://cloud.langfuse.com` | Langfuse endpoint; self-hosted instances override it |

## 6. Verify your setup (2 minutes)

1. `curl localhost:8000/healthz` → `{"status":"ok"}` (process is alive).
2. `curl localhost:8000/api/health` → every component you expect is `"ok"`
   (this is what the UI's header dot polls every 10 s). In Mode A, expect
   `"qdrant": {"status": "error", "detail": "All connection attempts
   failed"}` — that is the correct, expected shape of "degraded" with no
   Qdrant running, not a bug (see Troubleshooting below).
3. Open http://localhost:8000/ → send `ping` → tokens stream, a stats line
   appears under the answer. *That stats line is the same one every other
   chapter in this handbook points back to.*
4. `uv run pytest -q` → `573 passed` (fully offline, ~25 s).

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `"qdrant": {"status": "error", "detail": "All connection attempts failed"}` in `/api/health` | Mode A has no Qdrant container running | expected in Mode A; `docker compose up -d` for Mode B, or ignore if you meant to run offline |
| `ValueError: ASSISTANT_LLM_API_KEY is required for provider 'openai'` at startup | `.env` sets `ASSISTANT_LLM_PROVIDER=openai` without a key | set `ASSISTANT_LLM_API_KEY`, or switch back to `ASSISTANT_LLM_PROVIDER=fake` |
| WebSocket closes immediately, reason `missing or invalid token` | `ASSISTANT_AUTH_TOKEN` is set but the page was opened without `?token=` | open `http://localhost:8000/?token=<t>` once; the UI persists it |
| `401` `missing or invalid bearer token` from `curl .../api/documents` | `ASSISTANT_AUTH_TOKEN` is set; the request has no `Authorization: Bearer <t>` header | add the header, or unset the token for local dev |
| `429`, `rate limit reached — too many messages. Try again in Ns…` | more than `ASSISTANT_RATE_LIMIT_TURNS_PER_MINUTE` turns in a minute (default 20) | wait, or raise `ASSISTANT_RATE_LIMIT_TURNS_PER_MINUTE` |
| `400`, `no usable documents in the request. Skipped: […]` from `POST /api/documents` | uploaded a file whose suffix isn't `.md`/`.txt`/`.rst`/`.markdown`, or the bytes weren't UTF-8 text | convert or rename the file; the skip reason is in the same response |

## 8. Reading it honestly

- **Mode A's memory is genuinely gone on restart**, not just slow to load:
  fakeredis backs the same `SessionStore` interface with a plain in-process
  dict, so every session, rolling summary and audit row disappears with the
  process. That is a property of choosing not to run Redis, not a bug.
- **The rate limiter's identity is coarse when auth is off.** Without
  `ASSISTANT_AUTH_TOKEN`, both limiters key on `request.client.host`
  ([api/rate_limit.py](../../src/assistant/api/rate_limit.py)) — every
  client behind the same proxy or NAT shares one bucket.
- **`.env` is read once, at process start.** `Settings` is a plain
  `pydantic-settings` model with no live reload
  ([config.py](../../src/assistant/config.py)); editing `.env` while the
  server is running changes nothing until it restarts, and the health dot
  cannot tell you that your edit hasn't taken effect yet.
- **Nothing checks that a later mode's assumptions match an earlier mode's
  data.** Ingesting with one embedder, then switching
  `ASSISTANT_EMBEDDING_PROVIDER` and asking a question without re-ingesting,
  is a configuration mistake this chapter does not catch for you — chapter 05
  explains what actually happens to the collection.

## 9. Related

- [01 — Project overview](01-project-overview.md) — the architecture these run modes bring up
- [03 — Every technology](03-technologies.md) — why each piece of infrastructure here was chosen
- [05 — RAG & Qdrant](05-rag-qdrant.md) — what happens inside "filling the knowledge base"
- [reference/testing.md](../reference/testing.md) — the feature-by-feature manual checklist for the mode you're in
- [reference/localhost.md](../reference/localhost.md) — every URL the full stack exposes, one page
- [project/demo-runbook.md](../project/demo-runbook.md) — the demo-day sequence built on these modes
