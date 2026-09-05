# Info — the complete operator's handbook

**What this handbook covers: everything about running this project, one page
per subsystem — what it is, how to start it in any mode, every technology and
every `.env` variable, and how to watch it work end to end.** It is not the
concept explanations of what an LLM, embedding, agent or MCP server *is* —
that is [theory/](../theory/README.md); this folder assumes those concepts
and focuses on *this* project. Written to be read top-to-bottom once, then
used as a reference.

Two companion folders:
- **[theory/](../theory/README.md)** — concept explanations *from zero* (what an
  LLM/embedding/agent/MCP even is). Read a theory chapter when a concept here
  is new to you; this folder assumes them and focuses on *this* project.
- **[docs/](../)** — deep single-topic references:
  [tools.md](../reference/tools.md) (full tool schemas),
  [testing.md](../reference/testing.md) (manual click-through checklist),
  [backend-comparison.md](../reference/backend-comparison.md),
  [workshop.md](../project/workshop.md).

## 1. The chapters

| # | Chapter | What you'll be able to do after it |
|---|---|---|
| 01 | [Project overview](01-project-overview.md) | Explain what the platform is, draw its architecture, walk one message end-to-end |
| 02 | [Getting started](02-getting-started.md) | Start it in any of the 4 modes; understand every `.env` variable |
| 03 | [Technologies](03-technologies.md) | Say what each technology does, why it was chosen, where it lives in the code |
| 04 | [LLM, models, tokens & cost](04-llm-models-tokens.md) | Switch providers/models, read token/cost numbers, survive rate limits and llama quirks |
| 05 | [RAG & Qdrant](05-rag-qdrant.md) | Ingest docs, explain hybrid retrieval + rerank + relevance gate, browse Qdrant, measure quality |
| 06 | [Tools & MCP](06-tools-mcp.md) | List every tool the agent has, explain how a call executes, add a new one |
| 07 | [Observability](07-observability.md) | Open every dashboard, follow one turn through logs → metrics → traces → audit |
| 08 | [Agents, memory & WebSocket](08-agents-memory-ws.md) | Explain the 3 backends, the WS frame protocol, and rolling summarization |
| 09 | [Testing & operations](09-testing-operations.md) | Run the 573-test suite and auth mode; fix the common failures |

## 2. Every localhost URL (quick reference)

Start commands are in [02-getting-started.md](02-getting-started.md); this is
the short map. The **full page** — including how to browse the Qdrant
collection, read logs, and use each dashboard — is
[reference/localhost.md](../reference/localhost.md).

| URL | What it is | Needs |
|---|---|---|
| http://localhost:8000/ | **Chat UI** (Vue) — health dot, streaming chat, per-turn stats, "details" timeline | API server |
| http://localhost:8000/dev | Minimal built-in dev console (raw WS frames) | API server (debug mode) |
| http://localhost:8000/docs | OpenAPI/Swagger for the HTTP API | API server |
| http://localhost:8000/healthz | Liveness probe: `{"status":"ok"}` | API server |
| http://localhost:8000/api/health | **Deep health**: Redis/Qdrant ping + latency, LLM, MCP tools | API server |
| http://localhost:8000/api/info | Platform info: backends, provider, retrieval mode | API server |
| http://localhost:8000/metrics | **Prometheus metrics** (all `assistant_*` series) | API server |
| http://localhost:8000/api/documents | **Knowledge base**: `GET` lists indexed documents, `POST` adds them (multipart `files=` and/or `text=`+`source=`), `DELETE /{source}` removes one | API server |
| http://localhost:8000/api/sessions/{id}/turns | **Audit trail**: per-turn stats + event timeline | API server |
| http://localhost:8000/api/sessions/{id}/turns/{turn_id} | One turn — what the UI's "details" panel fetches | API server |
| http://localhost:16686 | **Jaeger** — trace waterfalls (service `ai-workspace-assistant`) | observability profile |
| http://localhost:3000 | **Grafana** — provisioned dashboard *AI Workspace Assistant*, no login | observability profile |
| http://localhost:9090 | **Prometheus** — raw queries; `/targets` shows scrape status | observability profile |
| http://localhost:6333/dashboard | **Qdrant web UI** — browse the `docs` collection and its points | `docker compose up` |
| http://localhost:5173 | Vite dev server (frontend hot reload, proxies WS to :8000) | `npm run dev` |

Ports without a UI: Redis `6379` (inspect with `docker exec -it
bench_project-redis-1 redis-cli`), Qdrant gRPC `6334`, Jaeger OTLP ingest
`4318` (where the app sends spans).

## 3. The 60-second mental model

FastAPI serves a WebSocket chat. Each user message becomes an **agent turn**:
an LLM (a deterministic fake by default, OpenAI's gpt-4.1-nano in the real profile) reasons in a
loop, calling **tools** — `search_docs` (RAG over the knowledge base in
Qdrant, which starts empty and is filled at runtime through the Documents
panel or `POST /api/documents`), `fetch_url` (public web/GitHub), and MCP
servers (code search, GitHub mock) — until it has an answer, which streams
back token by token. Redis keeps
sessions and conversation memory (rolling summarization). Every step is
observable: structured logs with correlation IDs, Prometheus metrics, OTel
spans to Jaeger, per-turn stats + cost in the UI, and a replayable audit
trail. Three interchangeable agent runtimes (custom loop / Pydantic AI /
LangGraph) implement the same contract, switchable per session.

## 4. Related

- [docs/README.md](../README.md) — the top-level index; this handbook is one of the five folders it points into
- [theory/README.md](../theory/README.md) — the concepts this handbook assumes, explained from zero
- [handbook/01 — Project overview](01-project-overview.md) — where the top-to-bottom read through this folder actually starts
- [reference/tools.md](../reference/tools.md) — the full tool schemas behind chapter 6's inventory
- [project/documentation-standard.md](../project/documentation-standard.md) — the fourteen rules (adopted 2026-09-04) every chapter in this folder is written to
