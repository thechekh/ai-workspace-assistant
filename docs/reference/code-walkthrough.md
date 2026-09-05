# Code walkthrough — one question, end to end

**One real turn — "How is todometer released?", turn `b099e9cd40ff`, sent
on 2026-09-04 — followed through every layer of the system in execution
order, naming the exact file and line at each step, with the question a
reviewer is most likely to ask there and its answer.** For what each layer
*is*, read [handbook/01](../handbook/01-project-overview.md) first; for the
concepts, [theory/](../theory/README.md). This page is the code, read with
the repository open.

## 1. What the walkthrough is

By the end you will have touched the app factory, the WebSocket protocol,
rate limiting, cancellation, conversation memory, the agent loop, the LLM
client and its provider hardening, the tool seam, the whole RAG pipeline,
telemetry, persistence and the audit trail. That is the entire system, and
one question exercises all of it. The turn's own log lines are the capture
in §5; its numbers — 2 LLM steps, one tool call, 1,003 ms of retrieval,
8,380 prompt and 175 completion tokens, $0.000908, 4,455 ms end to end —
recur below where each layer produced them.

**The 10-minute version.** If you only have ten minutes before you present,
read these six:

1. [`api/ws.py` → `_handle_turn`](../../src/assistant/api/ws.py#L173) — the conductor
2. [`agent/backends/custom.py` → `CustomAgent.run`](../../src/assistant/agent/backends/custom.py#L53) — the agent loop, 45 lines
3. [`agent/tools/base.py` → `Tool.run`](../../src/assistant/agent/tools/base.py#L52) — the one seam every tool call passes through
4. [`rag/retriever.py` → `search`](../../src/assistant/rag/retriever.py#L43) — retrieve → rerank → gate
5. [`llm/client.py` → `stream_step`](../../src/assistant/llm/client.py#L334) — the provider hardening
6. [`telemetry.py` → `InstrumentedLLM`](../../src/assistant/telemetry.py#L103) — how every number gets measured

Line numbers are checked by a test that fails when an anchor points at a
blank line, but they still drift as code moves; the symbol name in each
heading always finds the spot.

## 2. The turn, step by step

### Step 0 — The app boots

**[`main.py` → `create_app`](../../src/assistant/main.py#L174)** and
**[`build_runtime`](../../src/assistant/main.py#L93)**

`create_app` is a factory, not a module-level app. Everything a request
needs is assembled once in `build_runtime` into a
[`Runtime` dataclass](../../src/assistant/main.py#L53) — Redis, the LLM
client, the session store, memory, the three agent backends, the HTTP
client, Qdrant, the MCP registry — and released in order by
`Runtime.aclose`. Two deliberate details: the keyword overrides
(`redis_client=`, `llm=`, `retriever=`) let tests substitute whole
collaborators — fakeredis, `FakeLLM`, in-memory Qdrant — without the factory
growing `if x is None` branches; and
[`__getattr__`](../../src/assistant/main.py#L241) builds the app lazily, so
`uvicorn assistant.main:app` still works while *importing* the module no
longer reads a developer's `.env`, reconfigures logging or installs a
tracer. That was a real bug: the suite was picking up local `.env` files.

**If asked — "why a factory instead of a module-level `app`?"** Because a
module-level app runs its side effects at import time, and the test suite
imports the module. Lazy construction is what lets the suite run with no
`.env`, no network and no containers.

### Step 1 — A browser connects

**[`api/ws.py` → `chat_endpoint`](../../src/assistant/api/ws.py#L40)**

The connection is accepted, then optionally authenticated: browsers cannot
set WebSocket headers, so the token arrives as `?token=` and is compared
with `secrets.compare_digest`, not `==` — a plain comparison returns on the
first differing byte and leaks the secret to a timing attack. `?backend=`
picks the runtime for this connection (this turn: `custom`) and
`?session_id=` resumes a conversation. The server replies with one
`session` frame; the log's first line, `ws.connected`, carries the session
id every later line inherits.

**If asked — "why is the token in the URL? Isn't that leaky?"** It is, and
it is a known trade-off: query strings land in access logs. The browser API
cannot set headers, so the alternatives are a cookie or a short-lived ticket
endpoint; for a single-tenant internal tool behind a gateway this is
acceptable, and [security.md](security.md) lists it.

### Step 2 — The message arrives, and the loop stays free

**[`chat_endpoint`'s receive loop](../../src/assistant/api/ws.py#L79)**

```
raw = await websocket.receive_text()
incoming = TypeAdapter(ClientMessage).validate_json(raw)
```

`ClientMessage` is a discriminated union of `UserMessage | CancelRequest`
([schemas.py](../../src/assistant/api/schemas.py)); invalid JSON produces an
`error` frame and the socket survives. The structural decision is a few
lines down: the turn is started as an **`asyncio.Task`**, not awaited
inline. A loop that awaits the answer cannot read the next frame, and
reading the next frame is the only way a `cancel` can arrive mid-stream. A
[done-callback](../../src/assistant/api/ws.py#L163) retrieves the task's
exception so a failure cannot vanish into asyncio's "Task exception was
never retrieved" at garbage-collection time.

**If asked — "why WebSockets and not SSE?"** This step is the answer. SSE
is one-way; stopping a turn would mean dropping the connection. The `cancel`
frame needs a channel back to the server, which is what a WebSocket is.

### Step 3 — The budget guard

**[`_within_rate_limit`](../../src/assistant/api/ws.py#L142)** →
**[`RateLimiter.check`](../../src/assistant/api/rate_limit.py#L49)**

Checked *before* the turn starts, so a runaway client costs one Redis
round trip instead of an LLM call. A sliding-window log in a sorted set,
not `INCR`+`EXPIRE`: a fixed window lets a burst across the boundary
through at twice the limit. Refused requests are removed from the log
again, so being throttled never pushes your own reset further away. This
turn was the session's first, so the check passed silently.

**If asked — "is that your rate limiting or the provider's?"** Both exist
and they are different. This one is *yours*, protecting your budget from a
stuck client; the provider's 429 is handled separately in step 7.

### Step 4 — The conductor

**[`_handle_turn`](../../src/assistant/api/ws.py#L173)**

Owns the socket, the `agent.turn` span, error mapping and persistence. The
*accounting* lives elsewhere, in
[`TurnRecorder`](../../src/assistant/api/turn_recorder.py#L30): feed it each
event with [`observe`](../../src/assistant/api/turn_recorder.py#L55), then
ask for a [`summary`](../../src/assistant/api/turn_recorder.py#L97) (the
wire frame) and a [`record`](../../src/assistant/api/turn_recorder.py#L119)
(the audit row). It touches neither socket nor Redis, so first-token
latency, token totals and cost are unit-testable without a live socket. The
log's `turn.start … user_chars=26` is this function opening the span.

A turn ends exactly one of three ways, and **all three send a `turn`
frame**: completed, `cancelled`, or `failed`. That makes the frame a usable
end-of-turn marker, and it keeps spend visible when a provider fails after
retries — returning early there once hid the cost of three prompts.

### Step 5 — Building the prompt

**[`ConversationMemory.context_for`](../../src/assistant/memory/conversation.py#L35)**

The full transcript lives in Redis untouched; what the *model* sees is
bounded:

```
system prompt + [rolling summary] + last N verbatim messages + new question
```

When the un-summarized tail exceeds `history_char_budget` (8,000
characters), everything but the last `history_keep_recent` (6) messages is
folded into a persisted summary, so each message is summarized at most
once. The user message is appended to
[`SessionStore`](../../src/assistant/memory/session.py#L48) *before* the
agent runs, in the same pipeline that updates the recency index behind the
Chats panel. This turn's prompt was 8,380 tokens across both steps — the
system prompt, the tool schemas, the question, and after step 8 the tool
result.

**If asked — "what stops the prompt growing forever?"** This function, and
a test proves it: after enough turns the prompt size stops growing,
identically on all three backends.

### Step 6 — The agent loop

**[`CustomAgent.run`](../../src/assistant/agent/backends/custom.py#L53)**

45 lines, no framework, and worth reading in full because it *is* the ReAct
pattern: stream an LLM step; text deltas become `TokenEvent`s forwarded to
the browser immediately; if the model requested no tools, yield
`FinalEvent`; otherwise execute each tool, append the results as
`role="tool"` messages, and loop, bounded by `max_iterations` (6), which
returns an honest message rather than looping forever. This turn went
round twice: step one produced a `search_docs` call, step two the answer.

**If asked — "why write the loop yourself when frameworks exist?"** Because
this is the mechanism the frameworks wrap, and 45 readable lines you can
debug beat a black box for a system you have to defend. Both frameworks are
*also* implemented here against the same
[`AgentBackend` protocol](../../src/assistant/agent/base.py#L68);
[backend-comparison.md](backend-comparison.md) measured the same question
on all three at about $0.0009 each.

### Step 7 — The LLM call, and the hardening around it

**[`InstrumentedLLM.stream_step`](../../src/assistant/telemetry.py#L103)**
wraps **[`OpenAICompatibleLLM.stream_step`](../../src/assistant/llm/client.py#L334)**

Every provider here speaks the OpenAI-compatible API, so one client covers
`openai`, `ollama` and `gemini` and the provider is a config value. The
wrapper is the single telemetry seam: one span and one set of metrics per
LLM step, real token usage when the provider reports it and a `chars/4`
estimate when it does not; its `finally` block records cost and latency
even when the turn is abandoned mid-stream. In the log this step is the
`POST https://api.openai.com/v1/chat/completions` line at `11:51:48.572`,
and again at `11:51:50.901` for the answer.

Inside the client, three pieces of hardening that all came from real
failures: [`_create_stream`](../../src/assistant/llm/client.py#L404) — 429
backoff honouring `Retry-After` (`retry-after: 0` is valid and means "retry
now", so it is tested against `None`, never truthiness) and a retry without
`stream_options` for providers that reject it; a `tool_use_failed` retry
and salvage — some OpenAI-compatible providers abort the stream on a
malformed tool call, so the step is retried twice and then the model's
attempt is recovered from the `failed_generation` payload; and
[`_LeakedTextBuffer`](../../src/assistant/llm/client.py#L227) — some models
print a tool call as prose (`<function=name>{...}`), so leading text is
withheld only while it could still be that markup, and
[`parse_leaked_tool_calls`](../../src/assistant/llm/client.py#L132)
recovers all four opener variants seen in the wild.

**If asked — "what breaks with real models that didn't with fakes?"**
Exactly these three: the fake provider never rate-limits or emits broken
JSON, so the failures the hardening exists for are the ones a fake cannot
test. It is also why the Pydantic AI backend, which bypasses this client,
had to re-implement the retries.

### Step 8 — The tool call

**[`ToolRegistry.execute`](../../src/assistant/agent/tools/base.py#L111)** →
**[`Tool.run`](../../src/assistant/agent/tools/base.py#L52)**

Every tool call from every backend, native or MCP, funnels through
`Tool.run`. That single seam owns the duplicate-call guard (the same call
twice in one turn returns a pointer to the first result instead of
re-executing), the span, the metrics and the structured log, the 20,000-
character cap on results, and crash isolation — an exception becomes an
`error:` *result*, so a broken tool never ends the turn. An unknown tool
name is refused and counted under a fixed `<unregistered>` label, because
a hallucinated name would otherwise mint a Prometheus series that never
goes away. This turn's line: `tool.executed tool=search_docs status=ok
duration_ms=1018 result_chars=2012` — 15 ms of seam over the retrieval
inside it. [tools.md §6](tools.md) shows every guard failing on purpose.

**If asked — "how do you stop the model doing something dangerous?"** This
function. Names are allowlisted, arguments are schema-shaped, execution is
server-side, every tool is read-only except one additive write, and nothing
is `eval`'d.

### Step 9 — Retrieval

**[`make_search_docs`](../../src/assistant/agent/tools/search_docs.py#L100)** →
**[`Retriever.search`](../../src/assistant/rag/retriever.py#L43)**

The pipeline, in order, with this turn's timings:

1. **Embed the query** — in the real profile `text-embedding-3-small`, the
   `POST …/v1/embeddings` at `11:51:49.605`, most of the retrieval's second;
   offline, [`HashEmbedder`](../../src/assistant/rag/embeddings.py#L32) does
   signed feature hashing into 512 dimensions, free and deterministic.
   [`build_embedder`](../../src/assistant/rag/embeddings.py#L98) chooses from
   config.
2. **Sparse-encode it** ([`sparse.py`](../../src/assistant/rag/sparse.py)),
   with a tokenizer that splits `completedPercentage` into its words so
   identifiers match literally.
3. **Query Qdrant once** —
   [`VectorStore.search`](../../src/assistant/rag/store.py#L128) issues dense
   and sparse prefetches and fuses them server-side with RRF: the
   `POST …/points/query` at `11:51:49.627`, 22 ms later answered.
4. **Rerank** — [`LexicalReranker`](../../src/assistant/rag/rerank.py#L51)
   reorders the 20 candidates and keeps 4. This is the stage that measurably
   earns its place: +0.11 recall@1 ([metrics.md](metrics.md)).
5. **Gate** — chunks sharing no meaningful token with the query are dropped
   via [`query_overlap`](../../src/assistant/rag/rerank.py#L22), so "nothing
   relevant" is trustworthy. Here all four survived:
   `rag.retrieved mode=hybrid results=4 duration_ms=1003 top_score=0.643`,
   top source `cassidoo/todometer/RELEASE-DOCS.md`.

What is *in* the index got there through
[`chunk_markdown`](../../src/assistant/rag/chunking.py#L73) — heading-aware,
the breadcrumb prefixed to the embedded text — and
[`ingest_chunks`](../../src/assistant/rag/ingest.py#L38), which deletes a
source before re-adding it: deterministic ids alone were not enough, because
shortening a document left orphaned chunks that stayed searchable.

**If asked — "how do you know retrieval is any good?"** It is measured and
gated in CI: recall@1 0.83, recall@5 1.00, MRR 0.92 on the 18-question
golden set, every row re-measured on 2026-09-05 in [metrics.md](metrics.md).

### Step 10 — Second step, final answer

The 2,012-character tool result goes back as a `role="tool"` message and
the loop runs again. This time the model answers in text — *"The files that
describe how to release todometer are primarily documented in the
RELEASE-DOCS.md file…"* — the loop yields `FinalEvent`, the output guard
checks it for false completion claims, and `_handle_turn` appends the answer
to the session history.

### Step 11 — What the browser received

In order: `session` → `token`… → `tool_call` → `tool_result` → `token`… →
`final` → `turn`. Typed in [`schemas.py`](../../src/assistant/api/schemas.py)
and mirrored in [`frontend/src/types.ts`](../../frontend/src/types.ts), so
the protocol and the agent contract cannot drift apart. The Pinia store
([`stores/chat.ts`](../../frontend/src/stores/chat.ts)) reduces those frames
into the transcript; Dev mode reveals the tool cards and the stats line,
Standard mode hides them, presentationally only. The first token reached
the browser at 4,048 ms — after the search, because the model wrote nothing
before deciding to call a tool.

### Step 12 — Accounting and persistence

`summary()` produces the `turn` frame — `llm_steps=2`, `tool_calls=['search_docs']`,
`prompt_tokens=8380`, `completion_tokens=175`, `cost_usd=0.000908`,
`duration_ms=4455` — and `record()` adds the event timeline, stored by
[`append_turn`](../../src/assistant/memory/session.py#L120) and capped at 50
turns per session. `GET /api/sessions/{id}/turns/{turn_id}` serves it back;
that is what the *details* panel renders. The same numbers become the
`turn.summary` log line and the Prometheus counters. Cancelled and failed
turns are excluded from the latency histogram (a provider timeout is not
your system's latency) but never from cost.

## 3. Where it lives in this project

| Layer | File | What the turn did there |
|---|---|---|
| app factory | [main.py](../../src/assistant/main.py) | assembled once at boot; nothing per turn |
| socket and turn | [api/ws.py](../../src/assistant/api/ws.py) | accepted, validated, rate-checked, ran the turn as a task, sent 4 kinds of frame |
| accounting | [api/turn_recorder.py](../../src/assistant/api/turn_recorder.py) | counted 2 steps, 1 tool, 8,555 tokens, $0.000908 |
| memory | [memory/conversation.py](../../src/assistant/memory/conversation.py), [memory/session.py](../../src/assistant/memory/session.py) | built the bounded prompt; appended question and answer |
| agent loop | [agent/backends/custom.py](../../src/assistant/agent/backends/custom.py) | two iterations |
| LLM client | [llm/client.py](../../src/assistant/llm/client.py), [telemetry.py](../../src/assistant/telemetry.py) | two provider calls, usage recorded from the provider's own counts |
| tool seam | [agent/tools/base.py](../../src/assistant/agent/tools/base.py) | one `search_docs` call, 1,018 ms, status ok |
| retrieval | [rag/retriever.py](../../src/assistant/rag/retriever.py), [rag/store.py](../../src/assistant/rag/store.py), [rag/rerank.py](../../src/assistant/rag/rerank.py) | embed, one Qdrant query, rerank 20→4, gate |
| observability | [observability.py](../../src/assistant/observability.py#L111) | four spans exported to Jaeger, Logfire and Langfuse |

## 4. How to run it

```sh
# the real profile: copy .env.production.example to .env, add the OpenAI key, then
docker compose --profile observability up -d
uv run uvicorn assistant.main:app

# send the question from the chat at http://localhost:8000/, or from a shell:
uv run python -c "
import asyncio, json, websockets
async def main():
    async with websockets.connect('ws://127.0.0.1:8000/chat') as ws:
        await ws.recv(); await ws.send(json.dumps({'type': 'user_message', 'content': 'How is todometer released?'}))
        while (f := json.loads(await ws.recv()))['type'] != 'turn': pass
        print(f)
asyncio.run(main())
"
```

The log lines in §5 appear in the uvicorn terminal as the turn runs. The
question assumes the todometer repository was ingested (`Ingest
github.com/cassidoo/todometer and include the code`, once); with the fake
provider the same sequence runs offline in under a second, with a scripted
answer.

| Run | Wall clock | Cost |
|---|---|---|
| this turn, real profile | 4.5 s | $0.0009 |
| the same question, fake provider | under 1 s | nothing |

## 5. How to see it

![Gateway log lines for the walkthrough's turn: turn.start, the first LLM call, embeddings, one Qdrant query, rag.retrieved, tool.executed, the second LLM call, turn.summary](../images/tools-turn-log.png)

Line by line, mapped to the steps above:

- **`turn.start … user_chars=26`** — step 4, the conductor opening the span.
- **`POST …/chat/completions`** at `11:51:48.572` — step 7, LLM step 1,
  which returned a tool call rather than text.
- **`GET …/collections/docs/exists`** — step 9's first touch of Qdrant,
  inside the tool.
- **`POST …/v1/embeddings`** — step 9.1, the query becoming a vector.
- **`POST …/collections/docs/points/query`** — step 9.3, one hybrid query,
  fused server-side.
- **`rag.retrieved … results=4 … duration_ms=1003`** — steps 9.4–9.5 done:
  four chunks survived reranking and the gate.
- **`tool.executed … status=ok duration_ms=1018 result_chars=2012`** —
  step 8's seam closing.
- **`POST …/chat/completions`** at `11:51:50.901` — step 7 again, the answer.
- **`turn.summary …`** — step 12: every number the stats line shows.

![A Jaeger trace of one turn: agent.turn at the root, two llm.step spans, one tool.execute containing rag.retrieve — five spans in 1.52 s](../images/jaeger-trace-waterfall.png)

The same shape as a trace, captured 2026-08-07 before the cloud lenses were
enabled: `agent.turn` spans the whole 1.52 s; the first `llm.step` (1.07 s)
ends with a tool call; `tool.execute` (43 ms) wraps `rag.retrieve` (42 ms)
— an offline hash-embedder turn, hence the speed; the second `llm.step`
(394 ms) writes the answer. With Logfire on, the same tree gains the httpx
calls beneath each step ([logfire-langfuse.md](logfire-langfuse.md)).

![The provisioned Grafana dashboard: Turns, Tokens, Tool calls, Errors, turn rate and duration by backend, LLM step duration, tokens per minute — with no traffic yet](../images/grafana-dashboard.png)

Metrics are always on and scraped from `/metrics`; this is the provisioned
dashboard as it looks before any turn has run — every panel reads *No data*
except *Errors* at 0. Send the question above and the top row moves on the
next 5-second refresh.

## 6. Proving it

| Claim | Where it is proved |
|---|---|
| the protocol works on all three backends | [tests/test_ws.py](../../tests/test_ws.py), every scenario ×3 |
| the backends agree offline | [tests/test_fake_parity.py](../../tests/test_fake_parity.py) |
| provider errors map to useful text | [tests/test_llm_errors.py](../../tests/test_llm_errors.py) |
| the prompt stays bounded | `test_long_conversations_stay_bounded_by_summary` in [tests/test_memory.py](../../tests/test_memory.py) |
| the guards on the tool seam | [tests/test_tool_loop.py](../../tests/test_tool_loop.py), and [tools.md §6](tools.md) run live |
| retrieval quality | `evals/run_retrieval.py --memory --check`, gated in CI — [metrics.md](metrics.md) |
| groundedness | `evals/run_ragas.py --control`, on demand — [ragas.md](ragas.md) |
| the anchors on this page point at code | `test_line_anchors_in_docs_point_at_real_code` in [tests/test_docs_coverage.py](../../tests/test_docs_coverage.py) |
| the bugs a review found stay fixed | [tests/test_review_regressions.py](../../tests/test_review_regressions.py) |

And the turn itself is reproducible: the command in §4 produces the same
log lines, the same frame shape and, on the same model, a cost within a
few ten-thousandths of a cent.

## 7. Showing it live

The 10-minute version as a spoken walk, with the repository open:

1. `_handle_turn` — *"one function owns a turn: the span, the errors, the
   persistence; the accounting is a separate object so it can be tested
   without a socket."*
2. `CustomAgent.run` — *"forty-five lines; this is the whole agent loop the
   frameworks wrap."*
3. `Tool.run` — *"every tool call from every backend passes here; this is
   where a crash becomes an error result and a repeat is refused."*
4. `Retriever.search` — *"embed, one fused Qdrant query, rerank twenty to
   four, drop anything sharing no token with the question."*
5. `stream_step` in the client — *"three pieces of hardening, each from a
   real provider failure."*
6. `InstrumentedLLM` — *"one wrapper measures every LLM step; the stats line
   under every answer is this code."*

Then send the question and read the log aloud against §5.

## 8. Reading it honestly

- **One backend, one question.** The walk follows the custom loop; Pydantic
  AI and LangGraph reach the same seams by different routes
  ([backend-comparison.md](backend-comparison.md)), and a question that
  needs two tools adds a third LLM step.
- **Line anchors drift.** The coverage test only proves each anchor lands
  on a non-blank line; the symbol name is the durable reference. Six anchors
  were found 7–69 lines off in an audit on 2026-09-04 and corrected.
- **The trace capture predates the cloud lenses**, and the Grafana capture
  shows an idle dashboard; both are real but not current, and the standard
  lists their re-capture as debt.
- **Timings are one sample.** The 1,003 ms of retrieval is mostly one
  embeddings round trip to OpenAI; offline the same step takes tens of
  milliseconds, as the Jaeger capture shows.

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| an anchor on this page opens the wrong line | code moved since the anchor was written | search the file for the symbol in the heading; then fix the anchor — the coverage test only checks the line is not blank |
| the log shows `turn.start` but no `tool.executed` | the model answered without a tool, or the tool seam was never reached | check the first `POST …/chat/completions` response: with no tool call the turn ends at step 7 |
| `rag.retrieved … results=0` | the relevance gate dropped everything, or the repository was never ingested | ask the assistant to ingest it; the zero-result text lists what *is* indexed |
| `tool.executed … status=crash` | a tool raised; the exception is in `error: tool '<name>' failed: …` and a `tool.crashed` traceback | usually Qdrant or the network; the turn still finished |
| `turn.summary … usage_estimated=True` on the real profile | the stream was cut before the provider's usage chunk (Stop pressed) or a step was aborted and retried | expected in those two cases; otherwise the provider stopped reporting usage |
| the first token takes seconds | the model decided to call a tool before writing anything | expected; the stats line's `first token` counts from the question, not from the answer's start |

## 10. Related

- [handbook/01 — Project overview](../handbook/01-project-overview.md) — the same layers described rather than traced
- [tools.md](tools.md) — step 8 in full, with every guard shown failing
- [metrics.md](metrics.md) — what step 9 scores, and the re-measured table
- [backend-comparison.md](backend-comparison.md) — steps 6 and 7 on the other two runtimes
- [security.md](security.md) — the controls steps 1, 3 and 8 enforce
- [handbook/07 — Observability](../handbook/07-observability.md) — the spans, metrics and log lines this turn produced, lens by lens
