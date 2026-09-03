# Every localhost link — the running system, one page

Everything reachable in a browser or terminal once the stack is up, plus how
to read logs and browse the vector DB. Start commands live in
[getting started](../handbook/02-getting-started.md); the demo-day sequence
in [demo-runbook.md](../project/demo-runbook.md).

```sh
docker compose up -d redis qdrant                 # infra
docker compose --profile observability up -d      # jaeger + prometheus + grafana
uv run uvicorn assistant.main:app                 # the app on :8000
```

## The app — http://localhost:8000

| URL | What it is |
|---|---|
| http://localhost:8000/ | **Chat UI** — streaming chat, tool cards, per-turn stats + cost, "details" audit timeline, Documents panel, backend dropdown, Stop button |
| http://localhost:8000/docs | **Swagger / OpenAPI** — every REST endpoint, callable from the browser |
| http://localhost:8000/api/health | **Deep health** — Redis/Qdrant ping + latency, LLM provider/model, connected MCP servers and their tools |
| http://localhost:8000/api/info | Platform shape: backends, provider, retrieval mode, auth |
| http://localhost:8000/healthz | Liveness probe (`{"status":"ok"}`) |
| http://localhost:8000/metrics | **Prometheus metrics** — every `assistant_*` counter/histogram, live |
| http://localhost:8000/api/documents | Knowledge base: `GET` lists sources, `POST` uploads, `DELETE /{source}` removes one |
| http://localhost:8000/api/sessions | Recent conversations (the sidebar's data) |
| http://localhost:8000/api/sessions/{id}/turns | **Audit trail** — per-turn stats + full event timeline, replayable |
| http://localhost:8000/dev | Minimal raw-WS dev console (debug mode only) |

WebSocket endpoint (not a browser link): `ws://localhost:8000/chat`
(`?backend=custom|pydantic_ai|langgraph`, `?session_id=…` to resume,
`?token=…` when auth is enabled).

## Observability

| URL | What it is | How to use it |
|---|---|---|
| http://localhost:16686 | **Jaeger** — traces | Service `ai-workspace-assistant` → Find Traces → open a turn → the four-span tree `agent.turn → llm.step → tool.execute → rag.retrieve` with timings, tool names and statuses |
| http://localhost:3000 | **Grafana** — dashboard | No login (anonymous admin). Dashboards → *AI Workspace Assistant*: turns, p95 latency, tool calls by status, tokens & cost per turn, rate-limit hits |
| http://localhost:9090 | **Prometheus** — raw queries | Try `assistant_cost_usd_total`, `assistant_tool_calls_total`, `histogram_quantile(0.95, rate(assistant_turn_seconds_bucket[5m]))`; `/targets` shows scrape status |

The strongest single demo: ask one question in chat, then show the same turn
three ways — the UI's **details** timeline, the Jaeger trace, and the cost
tick in Grafana. One question, three lenses, one story.

## The vector DB — http://localhost:6333/dashboard

Qdrant's built-in web UI:

1. **Collections** → **`docs`** — see the schema itself: two named vectors
   per point, `dense` (1536-dim cosine in the real profile, 512 in dev) +
   `lexical` (sparse). That schema *is* the hybrid-search design, visible.
2. Open the collection → browse **points**: every chunk with its `source`
   (`owner/repo/path` for ingested repos), `heading` breadcrumb, and raw
   `text`.
3. The **Console** tab runs raw API calls — e.g.
   `POST collections/docs/points/scroll` with `{"limit": 10, "with_payload": true}`.

From the terminal:

```sh
curl http://localhost:6333/collections/docs      # schema + point count
curl http://localhost:8000/api/documents         # the app's view: sources + chunks
```

## Logs

| What | Where |
|---|---|
| App log — structured, every turn/tool/retrieval, correlated by `session_id`/`turn_id` | the uvicorn terminal (or the file it was redirected to); JSON lines with `ASSISTANT_LOG_JSON=true` |
| Per-turn audit (the friendliest view) | **details** under any answer in the UI — tools, arguments, result sizes, millisecond offsets; same data at `/api/sessions/{id}/turns` |
| Container logs | `docker logs -f bench_project-qdrant-1` (likewise `-redis-1`, `-jaeger-1`, `-prometheus-1`, `-grafana-1`) |
| Full prompt/completion dumps (dev only — conversations end up in logs) | set `ASSISTANT_LOG_PROMPTS=true` |

## Ports without a UI

| Port | What |
|---|---|
| 6379 | Redis — inspect with `docker exec -it bench_project-redis-1 redis-cli` (`KEYS session:*`, `TTL …`) |
| 6334 | Qdrant gRPC |
| 4318 | Jaeger OTLP ingest — where the app sends spans (`ASSISTANT_OTLP_ENDPOINT`) |
| 5173 | Vite dev server, only during `npm run dev` (hot reload, proxies to :8000) |
