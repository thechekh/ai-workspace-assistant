# 07 — Observability: watching everything work

The Phase-9 goal was *"every process explained, traceable, and controlled"*.
This chapter is the operator's tour: every surface, what it shows, and how to
follow a single message through all of them.

## Setup recap

Logs and metrics are **always on** (zero config). Traces need a destination:

```sh
docker compose --profile observability up -d      # Jaeger + Prometheus + Grafana
# .env: ASSISTANT_OTLP_ENDPOINT=http://localhost:4318   → restart server
```

| Surface | URL | Zero-account |
|---|---|---|
| Chat UI signals (dot, stats, details) | http://localhost:8000/ | ✅ |
| Deep health JSON | http://localhost:8000/api/health | ✅ |
| Prometheus metrics (raw) | http://localhost:8000/metrics | ✅ |
| Audit trail JSON | http://localhost:8000/api/sessions/{id}/turns | ✅ |
| Jaeger traces | http://localhost:16686 | ✅ |
| Grafana dashboard | http://localhost:3000 (anonymous admin) | ✅ |
| Prometheus UI | http://localhost:9090 (`/targets` = scrape status) | ✅ |
| Logfire / Langfuse cloud | set tokens in `.env` | account |

## 1) Structured logs — the narrative

[logs.py](../../src/assistant/logs.py): structlog renders **pretty console** in
dev, **JSON lines** with `ASSISTANT_LOG_JSON=true` (same pipeline reformats
uvicorn/stdlib logs; JSON mode renders exception tracebacks too).

**Correlation IDs**: the WS layer binds `session_id`, `turn_id`, `backend`
into context at turn start — *every* log line from the loop, tools, and RAG
carries them automatically. Grep one `turn_id` and you get the whole story:

| event | logger | says |
|---|---|---|
| `ws.connected` / `ws.disconnected` | assistant.ws | socket lifecycle |
| `turn.start` | assistant.ws | user message size |
| `rag.retrieved` | assistant.rag | mode, results, `top_source`, `top_score`, ms |
| `tool.executed` | assistant.tools | tool, status, ms, result size |
| `tool.crashed` / `tool.unknown` / `tool.duplicate_call` | assistant.tools | the failure modes |
| `turn.summary` | assistant.ws | **one line per turn**: duration, first-token ms, LLM steps + ms, tool list, tokens, `usage_estimated`, `cost_usd`, answer size |
| `turn.failed` | assistant.ws | mapped error `kind` + traceback |
| `llm.prompt` / `llm.completion` | assistant.telemetry | only with `ASSISTANT_LOG_PROMPTS=true` (dev-only — conversations land in logs) |

`turn.summary` is the greppable "what just happened" record — start there.

## 2) Metrics — the aggregates (`/metrics` → Prometheus → Grafana)

Defined in [telemetry.py](../../src/assistant/telemetry.py):

| Series | Labels | Meaning |
|---|---|---|
| `assistant_turns_total` | backend | chat turns processed |
| `assistant_turn_seconds` | backend | end-to-end turn latency (histogram) |
| `assistant_llm_step_seconds` | provider | one model step (histogram) |
| `assistant_tool_seconds` | tool | tool execution (histogram) |
| `assistant_tool_calls_total` | tool, status=`ok\|error\|crash\|duplicate\|unknown` | every call |
| `assistant_retrieval_seconds` | mode | RAG retrieval (histogram) |
| `assistant_tokens_total` | direction=`prompt\|completion` | tokens, real or estimated |
| `assistant_cost_usd_total` | model | indicative spend |
| `assistant_errors_total` | kind (see chapter 04 table + `invalid_message`, `agent_event`, `turn_exception`, `rate_limited`) | user-visible failures |
| `assistant_cancelled_turns_total` | backend | turns the user stopped (excluded from `turn_seconds`) |
| `assistant_rate_limited_total` | bucket=`turns\|writes` | requests the limiter refused |

PromQL you'll actually use (paste into :9090 or a Grafana panel):

```promql
sum by (backend) (rate(assistant_turns_total[5m]))            # traffic
histogram_quantile(0.95, sum by (le, backend)
  (rate(assistant_turn_seconds_bucket[5m])))                  # p95 latency
sum by (tool, status) (increase(assistant_tool_calls_total[1h]))
increase(assistant_cost_usd_total[1d])                        # $ today
sum by (kind) (increase(assistant_errors_total[1h]))          # what's failing
increase(assistant_cancelled_turns_total[1d])
  / increase(assistant_turns_total[1d])                       # stop rate: are answers too slow?
sum by (bucket) (increase(assistant_rate_limited_total[1h])) # who is hitting the limits
```

**Grafana** auto-provisions the *AI Workspace Assistant* dashboard
([observability/grafana/dashboards/assistant.json](../../observability/grafana/dashboards/assistant.json)):
stat tiles (turns/tokens/tool-calls/errors) + turn rate, p50/p95 by backend,
LLM p95 by provider, tokens/min, tool calls + p95, retrieval p95, errors —
5 s refresh. Prometheus scrapes `/metrics` every 5 s (host-run server via
`host.docker.internal:8000`; the `api:8000` target is for full-compose mode —
one of the two always shows down, that's expected).

## 3) Traces — the anatomy (Jaeger)

Manual OTel spans on exactly the seams that explain the agent:

```
agent.turn  (session.id, turn.id, agent.backend, tool_calls, answer_chars)
└─ llm.step        (provider, model, prompt_messages, duration, tokens, usage_estimated)
   └─ tool.execute (tool.name, tool.status, result_chars)
      └─ rag.retrieve (mode, rerank, candidates, results, top_score)
└─ llm.step        (the answer step)
```

MCP SDK client spans (`MCP send tools/call …`) appear nested too. Finding a
trace: Jaeger → service `ai-workspace-assistant` → operation `agent.turn` →
Find Traces; or take `turn <id>` from the UI stats-line tooltip and search by
tag. Destinations ([observability.py](../../src/assistant/observability.py)):
OTLP→Jaeger, Logfire, Langfuse — any combination, same spans; **no
destination configured = fully inert** (no-op tracer, zero overhead).

## 4) Per-turn stats in the chat (the product layer)

**Standard vs Dev.** The header carries a mode toggle. *Standard* is a plain
conversation — no tool cards, no stats. *Dev* reveals the instrumentation:
tool cards, the stats line below, and its expandable timeline. The choice
persists across reloads.

The distinction is presentational only: every frame is still received and
stored, so flipping to Dev reveals the numbers for messages **already on
screen**, and nothing has to be re-run. Standard mode still shows a quiet
"working…" hint while a tool is in flight, so the UI is never silently busy.

That split is deliberate — a demo audience wants the clean product, and an
engineer debugging a bad answer wants everything.

After every answer the server sends a `turn` WS frame; in Dev mode the UI
renders it under the message:

```
3.3s · first token 2995 ms · 2 LLM steps · 4313→115 tok · ~$0.0026 · fetch_url   details
```

A stopped turn ends the same line with `· stopped`, and the answer itself
carries a "stopped by you" marker **in both modes** — an answer cut short
must never read as a complete one just because the stats are hidden.

- tokens without `(est)` = provider-reported; `(est)` = chars/4 fallback.
- `~$` hidden when 0 (fake provider / unknown model).
- tooltip = `turn <id> · backend <name>` — the correlation key into logs and
  traces.
- **details** expands the audit timeline fetched from
  `/api/sessions/{id}/turns`:
  `+1284 ms tool_call fetch_url {...} → +2591 ms tool_result (1501 chars) →
  +3290 ms final (439 chars)`.

## 5) Deep health — the "controlled" part

`GET /api/health` actively probes each dependency (vs `/healthz` which only
proves the process lives): Redis ping + latency, Qdrant count + latency +
points, LLM provider/model, connected MCP tools. Overall `ok`/`degraded`.
The UI header dot polls it every 10 s: **green** ok · **amber** degraded
(hover for per-component status) · **gray** backend unreachable.

## 6) Audit trail — replay any turn

`GET /api/sessions/{id}/turns` (bearer-guarded when auth is on): last 50
turns per session, each with full stats (`usage_estimated`, `cost_usd`,
tools) and the event timeline with millisecond offsets. Stored in Redis with
the session TTL. This is what powers the UI's "details" — and your debugging
("what exactly did the agent do at 14:03?").

## Follow one message through every layer (the drill)

Ask *"Which service generates PDF invoices?"*, then:

1. **UI**: tool card `search_docs` → answer citing
   `architecture/services.md` → stats line → click *details* for the
   timeline. Hover the stats line, note the `turn_id`.
2. **Logs**: grep the `turn_id` → `turn.start` → `rag.retrieved
   (top_source=architecture/services.md, 43 ms)` → `tool.executed (ok)` →
   `turn.summary ($0.0012)`.
3. **Jaeger**: newest `agent.turn` trace → the 5-span waterfall with the
   same timings.
4. **Prometheus**: `assistant_turns_total` went up by 1;
   `assistant_tool_calls_total{tool="search_docs",status="ok"}` too.
5. **Audit**: `curl localhost:8000/api/sessions/<sid>/turns` → the same turn
   as JSON, replayable.

Same numbers, five angles — logs for narrative, metrics for aggregates,
traces for anatomy, stats for the user, audit for replay.
