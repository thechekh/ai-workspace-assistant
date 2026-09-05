# 07 — Observability: watching everything work

**What this chapter covers: every observability surface this project exposes
— logs, metrics, traces, per-turn stats, deep health, the audit trail — what
each shows, and how to follow one message through all of them.** It is not
the deep dive on the two cloud dashboards; for how Logfire and Langfuse
compare, what each one's screens show, and what enabling them revealed, see
[reference/logfire-langfuse.md](../reference/logfire-langfuse.md).

The Phase-9 goal was *"every process explained, traceable, and controlled"*.
This chapter is the operator's tour: every surface, what it shows, and how to
follow a single message through all of them.

## 1. Setup recap

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

### Where it lives

| File | Role |
|---|---|
| [logs.py](../../src/assistant/logs.py) | structlog pipeline: pretty console or JSON lines, correlation ids merged into every line |
| [telemetry.py](../../src/assistant/telemetry.py) | the Prometheus series, the four manual spans, `InstrumentedLLM`, the per-turn `TurnStats` |
| [observability.py](../../src/assistant/observability.py) | wires the OTLP/Logfire/Langfuse destinations and the noise-exclusion sampler |
| [api/turn_recorder.py](../../src/assistant/api/turn_recorder.py) | turns raw agent events into the `turn` WS frame and the persisted audit row |
| [api/routes.py](../../src/assistant/api/routes.py) → `health` | the deep-health probe behind `/api/health` |

### The two cloud lenses

Logfire (the application view) and Langfuse (the LLM view) receive the same
spans as Jaeger the moment their credentials are in `.env`; the startup log
line `tracing configured (otlp=…, logfire=…, langfuse=…)` says which are
live — the exact format string in
[observability.py](../../src/assistant/observability.py). What each one is
for, how they compare, how to enable them, what each dashboard shows per
backend, and what enabling them revealed — all in
[reference/logfire-langfuse.md](../reference/logfire-langfuse.md).

![The verification: startup line, per-backend observation types in Langfuse](../images/observability-verification.png)

Line by line:

- **The startup line** — `tracing configured (otlp=True, logfire=True,
  langfuse=True)`, confirmed live on 2026-09-04, all three destinations from
  one instrumentation.
- **Per-backend observation types in Langfuse** — the custom and LangGraph
  backends produce plain spans; the Pydantic AI backend additionally produces
  **GENERATION** observations carrying usage and cost, because Logfire
  instruments it directly.
- **What the check found** — the Pydantic AI backend's own stats line read
  **0 prompt tokens** and $0.000016 while Langfuse showed ~5,000 input tokens
  for the same call: it drives the provider through its own model layer and
  never passed through `InstrumentedLLM`. Fixed by
  `record_external_usage` in [telemetry.py](../../src/assistant/telemetry.py),
  so all three backends now agree with Langfuse on cost.

## 2. Structured logs — the narrative

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

A worked example, the real turn captured in
[reference/tools.md §2](../reference/tools.md) (2026-09-04, turn
`b099e9cd40ff`, *"How is todometer released?"*): `grep b099e9cd40ff` over
that run's log turns up `turn.start user_chars=26`, then
`rag.retrieved mode=hybrid results=4 duration_ms=1003`, then
`tool.executed tool=search_docs status=ok duration_ms=1018
result_chars=2012`, and finally `turn.summary … llm_steps=2 …
cost_usd=0.000908 duration_ms=4455` — four lines, one turn id, the whole
turn reconstructed without touching a trace backend.

## 3. Metrics — the aggregates (`/metrics` → Prometheus → Grafana)

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

That is 11 series in total — every one of them defined in the table above,
which is also every `assistant_*` name `telemetry.py` exports; nothing in
`/metrics` is undocumented. The same real turn from §2 moved three of them
in one shot: `assistant_turns_total{backend="custom"}` by 1,
`assistant_tool_calls_total{tool="search_docs",status="ok"}` by 1, and
`assistant_cost_usd_total{model="gpt-4.1-nano"}` by `0.000908`.

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

![The provisioned Grafana dashboard: Turns, Tokens, Tool calls, Errors, turn rate and duration by backend, LLM step duration, tokens per minute — with no traffic yet](../images/grafana-dashboard.png)

Line by line: this is the dashboard exactly as provisioned, **before any
turn has run** — every panel reads *No data* except *Errors*, which reads 0
because the counter exists from process start. Send one chat message and the
top-row stat tiles move on the next 5-second refresh; nothing on this panel
is faked or drawn by hand, it is simply captured at time zero.

## 4. Traces — the anatomy (Jaeger)

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
destination configured = fully inert** (no-op tracer, zero overhead) —
proved by the exact startup line `tracing disabled — no
OTLP/Logfire/Langfuse destination configured` when none of the three are set.

![A Jaeger trace of one turn: agent.turn at the root, two llm.step spans, one tool.execute containing rag.retrieve — five spans in 1.52 s](../images/jaeger-trace-waterfall.png)

Line by line — a real trace captured 2026-08-07, **before** the cloud lenses
were enabled, so this is the four-span design with nothing extra beneath it:

- **`agent.turn`** spans the whole 1.52 s — the root, and the span a search
  in Jaeger's UI is aimed at.
- **The first `llm.step`** (1.07 s) ends with a tool call rather than text.
- **`tool.execute`** (43 ms) wraps **`rag.retrieve`** (42 ms) — an offline
  hash-embedder turn, hence the speed; a real embedding call over the
  network is the 1,003 ms figure quoted in §2 instead.
- **The second `llm.step`** (394 ms) writes the answer.

With Logfire on, the same tree gains the httpx calls beneath each step —
`instrument_httpx()` in [observability.py](../../src/assistant/observability.py)
adds a client span per outgoing request, so a rebuilt version of this same
trace would show more than five spans without anything about the agent
having changed.

## 5. Per-turn stats in the chat (the product layer)

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

The real turn from §2 renders the same way: `4.455s · first token 4048 ms ·
2 LLM steps · 8380→175 tok · ~$0.0009 · search_docs`.

Field by field — this is the whole `turn` frame, and the audit record stores
the same values plus the timeline:

| Field | Meaning |
|---|---|
| `turn_id` | 12-hex id; the key for `GET /api/sessions/{id}/turns/{turn_id}` |
| `backend` | which runtime answered (`custom` / `pydantic_ai` / `langgraph`) |
| `duration_ms` | wall clock for the whole turn, question to final answer |
| `first_token_ms` | time to the first streamed character — the number a user actually feels; `null` if the turn produced no text |
| `llm_steps` | model round trips; 1 = answered directly, 2 = one tool call and a follow-up, and so on |
| `tool_calls` | the tools used, in order, including repeats |
| `prompt_tokens` / `completion_tokens` | tokens in and out, summed across every step of the turn |
| `usage_estimated` | `false` = the provider reported those counts; `true` = chars/4 fallback (see below) |
| `cost_usd` | indicative spend at listed prices; `0.0` for the fake provider and unpriced models |
| `cancelled` | the user pressed Stop |
| `failed` | the turn ended in an error (an `error` frame carries the message) |

A stopped turn ends the same line with `· stopped` and a failed one with
`· failed` — pinned by `tests/test_ws.py`'s
`test_cancel_stops_the_turn_and_reports_it` (`summary["cancelled"] is True`,
no `final` frame in the sequence) and
`tests/test_review_regressions.py`'s `test_a_failed_turn_still_reports_what_it_cost`
(`summary["failed"] is True`, `summary["completion_tokens"] > 0` — the
tokens spent before the crash are real and still counted). The answer itself
carries a "stopped by you" marker **in both modes** — an answer cut short
must never read as a complete one just because the stats are hidden.

**When `usage_estimated` is true.** Two cases, both honest rather than
broken: a stopped turn's stream is cut before the provider's final usage
chunk arrives; and a step the provider *aborted* — a provider's
`tool_use_failed`, say — reports no usage at all for that attempt. (The
Pydantic AI backend used to be a third case, because it never passes through
`InstrumentedLLM`; it now reports its run's usage into the same stats, per
§1, and only the offline fake model marks its counts as estimates.) The
last one has a consequence worth knowing when reading a cost dashboard: the
retried attempts really were billed, but the provider never reported them, so
`cost_usd` reads low on exactly those turns. `(est)` in the UI is the flag
that says "treat this number as approximate".

- tokens without `(est)` = provider-reported; `(est)` = chars/4 fallback.
- `~$` hidden when 0 (fake provider / unknown model).
- tooltip = `turn <id> · backend <name>` — the correlation key into logs and
  traces.
- **details** expands the audit timeline fetched from
  `/api/sessions/{id}/turns`:
  `+1284 ms tool_call fetch_url {...} → +2591 ms tool_result (1501 chars) →
  +3290 ms final (439 chars)`.

## 6. Deep health — the "controlled" part

`GET /api/health` actively probes each dependency (vs `/healthz` which only
proves the process lives): Redis ping + latency, Qdrant count + latency +
points, LLM provider/model, connected MCP tools. Overall `ok`/`degraded`.
The UI header dot polls it every 10 s: **green** ok · **amber** degraded
(hover for per-component status) · **gray** backend unreachable.

Proof, not just description: `tests/test_observability.py`'s
`test_deep_health_degrades_when_redis_is_down` wires a fake Redis whose
`ping()` raises `ConnectionError("redis unreachable")` and asserts the
response becomes `{"status": "degraded", "components": {"redis": {"status":
"error", "detail": "redis unreachable"}}}` — `detail` is always `str(exc)`
from [routes.py](../../src/assistant/api/routes.py), so a real outage's
wording depends on what actually failed underneath.

## 7. Audit trail — replay any turn

`GET /api/sessions/{id}/turns` (bearer-guarded when auth is on): last 50
turns per session, each with full stats (`usage_estimated`, `cost_usd`,
tools) and the event timeline with millisecond offsets. Stored in Redis with
the session TTL. This is what powers the UI's "details" — and your debugging
("what exactly did the agent do at 14:03?").

`tests/test_observability.py`'s `test_turns_audit_trail_records_timeline`
sends two turns (one that calls `search_docs`, one that answers directly)
and asserts the stored record's event list ends `..., "tool_call",
"tool_result", "final"` for the first and `[]` tool calls for the second —
the same shape `TurnRecorder.record()` builds from `TurnAuditEvent`s in
[api/turn_recorder.py](../../src/assistant/api/turn_recorder.py).

## 8. Follow one message through every layer (the drill)

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

## 9. Showing it live

About two minutes, real profile with the observability stack up
(`docker compose --profile observability up -d`, §1):

1. Run the drill's question in Dev mode and let it stream — *"watch the
   tool card appear, then the stats line — that's the same `turn` frame
   we're about to see from four other angles."* (~4-5 s on the real
   profile, under a second on the fake one.)
2. Click **details** — *"millisecond offsets, straight from the audit
   record Redis just stored — nothing here is re-computed."*
3. Switch to http://localhost:16686, find the newest `agent.turn` — *"same
   turn, same numbers, drawn as a waterfall instead of a line."*
4. Refresh http://localhost:3000 — *"five-second scrape; the top row just
   moved because of the message we sent thirty seconds ago."*
5. If Logfire/Langfuse tokens are set, open Langfuse's trace view for the
   same `turn.id` — *"three lenses on one turn, and they all agree on the
   cost because of a bug we had to go find"* (§1's usage-reporting fix).

## 10. Reading it honestly

- **Only four seams get a manual span.** `agent.turn`, `llm.step`,
  `tool.execute` and `rag.retrieve` are deliberate choices, not everything
  that happens — a slow embeddings call is invisible as its own span unless
  Logfire's `instrument_httpx()` is also on (§4). Without a cloud lens, a
  trace shows *that* a tool was slow, not which HTTP call inside it was.
- **Cost is indicative, not billing-accurate.** `estimate_cost_usd` prices
  from a small hardcoded table in
  [telemetry.py](../../src/assistant/telemetry.py)
  (`MODEL_PRICES_PER_MTOK`); a model that is not in that table prices at
  $0.0 silently — a real spend that simply does not appear on the cost
  dashboard rather than an error anyone sees.
- **`usage_estimated` turns can undercount real spend** — a retried step the
  provider aborted was genuinely billed but never reported (§5). The
  dashboard has no way to distinguish "free" from "billed but unreported"
  beyond the `(est)` flag.
- **The two captures here are dated and one predates a real change.**
  `jaeger-trace-waterfall.png` (§4) was captured 2026-08-07, before the
  cloud lenses added httpx child spans — a fresh capture would show more
  than five spans for the same turn. `grafana-dashboard.png` (§3) shows the
  dashboard with no traffic at all; it proves provisioning, not that the
  panels read sensibly under load.
- **The dual Prometheus target is confusing by design, not a bug** — in any
  single run exactly one of `api:8000` / `host.docker.internal:8000` is
  ever up, and Prometheus's own `/targets` page will always show one "down".

## 11. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Startup log reads `tracing disabled — no OTLP/Logfire/Langfuse destination configured` | none of `ASSISTANT_OTLP_ENDPOINT`, `ASSISTANT_LOGFIRE_TOKEN`, or the Langfuse key pair is set | set at least one, then restart — `.env` is only read at startup |
| No traces in Jaeger even though the log shows `tracing configured (otlp=True, …)` | the observability profile isn't up, or `ASSISTANT_OTLP_ENDPOINT` points at the wrong port | `docker compose --profile observability up -d`; confirm :16686 and :4318 are reachable |
| Prometheus target `api:8000` shows down at `/targets` | the server is running on the host, not inside the compose network | expected in that mode — `host.docker.internal:8000` is the live target; one target is always down by design |
| Dashboards fill with `GET /metrics` / `GET /api/health` spans | a build older than the noise-exclusion fix | update; `NOISY_PATHS` and `make_noise_sampler` live in [observability.py](../../src/assistant/observability.py) |
| `/api/health` shows `"redis": {"status": "error", "detail": "..."}` | Redis is unreachable — `detail` is always `str(exc)` from the ping | `docker compose up -d redis`, or check `ASSISTANT_REDIS_URL` |
| JSON logs show `"exc_info": true` but no traceback | a build older than the `format_exc_info` fix | pull latest |

## 12. Related

- [reference/logfire-langfuse.md](../reference/logfire-langfuse.md) — the two cloud lenses compared, wired, and verified dashboard by dashboard
- [handbook/06 — Tools & MCP](06-tools-mcp.md) — the `tool.execute` span and the guards this chapter's traces show firing
- [handbook/09 — Testing & operations](09-testing-operations.md) — the retrieval quality gate that sits alongside these runtime signals
- [reference/metrics.md](../reference/metrics.md) — the RAG-specific numbers `rag.retrieve` and `assistant_retrieval_seconds` feed into
- [tests/test_observability.py](../../tests/test_observability.py) — every claim in this chapter about the `turn` frame, `/metrics`, deep health and the audit trail, pinned offline
