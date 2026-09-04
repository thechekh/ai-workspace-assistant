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

### Enabling Logfire and Langfuse, step by step

Both are dormant until their credentials exist in `.env`; the code path in
[observability.py](../../src/assistant/observability.py) is the same one that
feeds Jaeger, so enabling them adds destinations without touching a line of
code. Free tiers of both cover a workshop many times over. Keep Jaeger on
while you try them — all three receive the same spans, which is the point.

**1. Logfire (the application view)**

1. Sign in at https://logfire.pydantic.dev (GitHub or Google) and create a
   project — the name is yours, the service will report itself as
   `ai-workspace-assistant`.
2. Project settings → **Write tokens** → create one. It is shown once; copy
   it straight into `.env`, never into a chat or a commit:
   ```sh
   ASSISTANT_LOGFIRE_TOKEN=pylf_v1_...
   ```
3. Restart the gateway (`.env` is read at startup):
   ```sh
   uv run uvicorn assistant.main:app
   ```
   The startup log must say `tracing configured (otlp=True, logfire=True, …)`.
   With a token present, Logfire's SDK becomes the tracer provider and
   auto-instruments FastAPI (HTTP and the `/chat` WebSocket), httpx (every
   call to the LLM provider) and Pydantic AI.
4. Send one message in the chat, wait a few seconds (spans are batched),
   and open the project's **Live** view. Expect one `agent.turn` per message
   with `llm.step`, `rag.retrieve` and `tool.execute` nested inside, wrapped
   by the WebSocket span; each `llm.step` has an httpx child — the actual
   `POST …/chat/completions` with its status and duration. Click any span
   for its attributes: `agent.backend`, `session.id`, `turn.id`,
   `llm.model`, `rag.top_score`, `tool.status`, and so on.
5. Switch the backend dropdown to **Pydantic AI** and send another message.
   `instrument_pydantic_ai` adds the framework's own spans — agent run, model
   request with token counts, each tool call — which is the one-line
   instrumentation the framework comparison talks about.
6. Try the **Explore** tab: Logfire stores spans in a table you can query
   with SQL, e.g. every turn slower than two seconds:
   ```sql
   SELECT start_timestamp, duration, attributes->>'agent.backend' AS backend
   FROM records WHERE span_name = 'agent.turn' AND duration > 2
   ORDER BY start_timestamp DESC
   ```

**2. Langfuse (the LLM view)**

1. Sign in at https://cloud.langfuse.com (EU) or https://us.cloud.langfuse.com
   (US) and create an organization and a project. Self-hosting with Docker is
   also an option; only the host changes.
2. Project **Settings → API Keys → Create**: a public key `pk-lf-…` and a
   secret key `sk-lf-…`. Into `.env`:
   ```sh
   ASSISTANT_LANGFUSE_PUBLIC_KEY=pk-lf-...
   ASSISTANT_LANGFUSE_SECRET_KEY=sk-lf-...
   # only if your project is in the US region (the default is the EU host):
   ASSISTANT_LANGFUSE_HOST=https://us.cloud.langfuse.com
   ```
3. Restart the gateway; the log line now ends with `langfuse=True`. There is
   no Langfuse SDK involved: the same OpenTelemetry spans are exported to
   `<host>/api/public/otel/v1/traces`, authenticated with the two keys.
4. Send a message and open **Tracing → Traces**. Each `agent.turn` is one
   trace; its children are the observations, and the trace's attributes
   carry the session and turn ids, so filtering one conversation is a
   search for its `session.id`.
5. Know what each backend gives Langfuse. The project's own spans
   (`llm.step`, `tool.execute`, `rag.retrieve`) use project attributes such
   as `llm.model` and `llm.tool_calls`, so Langfuse shows them as plain spans
   with timings. Its **generation** view — prompt, completion, token counts,
   cost per model — fills in only for spans that follow the GenAI semantic
   conventions, which Logfire's Pydantic AI instrumentation emits. So with
   both tools enabled and the **Pydantic AI** backend selected, Langfuse
   shows the LLM calls as generations with cost; with the custom backend it
   shows the span tree and durations.
6. Once traces flow, the features that make Langfuse different are one menu
   away: **Sessions** (every turn of a conversation grouped), **Prompts**
   (versioned prompt management — the system prompt could live there), and
   **Scores** — a place to attach the Ragas faithfulness result
   ([reference/ragas.md](../reference/ragas.md)) to the traces it judged.

**3. Prove all three receive the same turn**

Send one message, then find its `turn.id` in three places: the span in
Jaeger (http://localhost:16686), the same span in Logfire's Live view, and
the trace in Langfuse. Same id, same tree, three different lenses — the
application, the LLM, and the raw waterfall.

**If nothing shows up**

| Symptom | Likely cause | Fix |
|---|---|---|
| Startup log says `logfire=False` / `langfuse=False` | the key is not in `.env`, or the server was not restarted | check the variable names above, restart |
| Logfire Live view stays empty | the token belongs to another project, or is a read token | create a *write* token in the project you are looking at |
| Langfuse shows nothing, log shows `401`/`403` on export | keys swapped, or wrong region host | public key is `pk-lf-`, secret is `sk-lf-`; set the US host if the project is there |
| Traces arrive late | spans are exported in batches every few seconds | wait, or stop the server — shutdown flushes |
| Everything works but Jaeger stopped | `ASSISTANT_OTLP_ENDPOINT` removed while editing `.env` | keep all three lines; they compose |

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
`· failed`. The answer itself carries a "stopped by you" marker **in both
modes** — an answer cut short must never read as a complete one just because
the stats are hidden.

**When `usage_estimated` is true.** Three cases, all honest rather than
broken: the pydantic-ai backend runs its own model layer and never passes
through `InstrumentedLLM`; a stopped turn's stream is cut before the
provider's final usage chunk arrives; and a step the provider *aborted* — a
a provider's `tool_use_failed`, say — reports no usage at all for that attempt. The
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
