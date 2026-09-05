# 09 — Observability & evals: trusting a nondeterministic system

**What this chapter answers: why an LLM application needs tracing and evals
on top of normal logging, how this project's four manual spans and offline
testing ladder make a nondeterministic system checkable, and where a real
judge model has to enter once determinism runs out.** It does not walk the
dashboards themselves — every metric, log event and PromQL query is
[handbook/07 — Observability](../handbook/07-observability.md).

## 1. Why LLM apps need more than normal logging

Three properties make LLM systems uniquely opaque:

1. **Nondeterminism** — the same input can produce different outputs; "works
   on my machine" means even less than usual.
2. **Cost is per token** — a prompt-building bug (say, resending unsummarized
   history) silently multiplies your bill without a single error log.
3. **Quality regressions are silent** — retrieval getting 10% worse throws
   no exception. You only see it if you *measure*.

The answer is two disciplines: **tracing** (see what actually happened) and
**evals** (measure whether it's any good).

## 2. Tracing with OpenTelemetry

OpenTelemetry (OTel) is the vendor-neutral standard for **traces**: a
request becomes a tree of timed **spans** (WS message → agent step → LLM
call → tool call → Qdrant query), each carrying attributes (model, token
counts, latency). Instrument once, export anywhere.

**In this project:**
[`observability.py`](../../src/assistant/observability.py) — one pipeline, two
specialized backends:

- **Logfire** (from the Pydantic team) — the *application* view: FastAPI
  requests, WS lifecycle, outgoing LLM HTTP calls, and one-line
  instrumentation of the Pydantic AI backend.
- **Langfuse** — the *LLM* view: generations with token costs, session
  replays, eval scores. It ingests the **same spans** via an OTLP exporter
  bolted onto the same pipeline — one instrumentation, two dashboards.

The design rule that matters here: **inert without keys.** No tokens
configured → the function logs one line and returns; nothing heavy is even
imported. Zero-config dev stays clean, and enabling tracing is purely a
`.env` change.

Four manual spans carry the whole story — chosen deliberately over relying
on auto-instrumentation, which shows HTTP calls, not agent decisions:

| Span | Opened in | Key attributes |
|---|---|---|
| `agent.turn` | [`api/ws.py`](../../src/assistant/api/ws.py), around one WS turn | `session.id`, `turn.id`, `agent.backend`, `turn.tool_calls`, `turn.answer_chars` |
| `llm.step` | [`telemetry.py`](../../src/assistant/telemetry.py), around one model round trip | `llm.provider`, `llm.model`, `llm.prompt_tokens`, `llm.completion_tokens`, `llm.usage_estimated` |
| `tool.execute` | [`agent/tools/base.py`](../../src/assistant/agent/tools/base.py), around one tool call | tool name, duration, status |
| `rag.retrieve` | [`rag/retriever.py`](../../src/assistant/rag/retriever.py), around one search | retrieval mode, result count, top score |

A worked example of the design earning its keep: this app polls its own
`/api/health` every 10 seconds and Prometheus scrapes `/metrics` every 5 —
left to auto-instrumentation, "those alone were 1,000+ traces an hour in
every cloud dashboard, burying the real turns and spending quota" (the exact
reasoning recorded in
[`observability.py`](../../src/assistant/observability.py)). Its
`make_noise_sampler()` installs a head sampler (`DropNoisyRootSpans`) that
drops exactly those root spans — matched by path for server spans, and by
`server.address` / `url.full` pointing at localhost for HTTP *client*
spans — while leaving every child span of a real turn alone, because a real
turn's own Qdrant call is never a *root* span. That last distinction is
deliberate: judging client spans by name alone would also drop a locally
served app's genuine turns along with the noise, which is exactly what an
earlier version of this sampler did.

## 3. The testing ladder (how you test nondeterminism: you remove it)

The whole strategy is: make every layer deterministic except the one you're
deliberately evaluating.

1. **Unit tests with scripted models** — `ScriptedLLM` plays exact
   sequences ("request this tool, then say this"), so every loop branch —
   happy path, unknown tool, malformed JSON, crashing tool, iteration limit
   — is asserted exactly ([`tests/test_tool_loop.py`](../../tests/test_tool_loop.py)).
2. **Protocol tests** — the WS suite drives full conversations against
   FakeLLM + fakeredis + in-memory Qdrant: streaming reassembly, session
   resume, summarization bounds, auth. Parametrized **×3 backends** — the
   same assertions must hold on custom, Pydantic AI, and LangGraph.
3. **Retrieval evals** — the golden set (18 annotated questions) scored
   with recall@k and MRR (chapter 03). Deterministic here too (hash
   embedder), so the numbers are reproducible to the digit: recall@1
   **0.83**, recall@5 **1.00**, MRR **0.92** for the default hybrid+rerank
   configuration (measured 2026-09-04, `uv run python evals/run_retrieval.py
   --memory`; full ablation table in
   [reference/metrics.md](../reference/metrics.md)).
4. **The browser, by hand and by script** — no Playwright suite lives in
   the repository; the UI is checked with the tiered manual checklist
   ([reference/testing.md](../reference/testing.md)), and the handbook's UI
   captures were taken by driving a headless browser against the real
   server.

573 tests, offline, in seconds, $0 (2026-09-05, `uv run pytest -q`). The
fakes aren't a compromise — they're what makes the suite *possible*.

## 4. What we deliberately do NOT test with fakes

Model *quality* — whether the model picks the right tool, retrieves the
right evidence, or writes a good answer. That boundary is intentional, and
it has two different answers on the far side of it, not one:

- **Retrieval quality** is measured, not tested pass/fail — step 3 above,
  and it is a CI gate because it is free and deterministic.
- **Answer faithfulness** needs a judge that reads the answer against the
  evidence, which needs a real model —
  [Ragas](../reference/ragas.md) scores it on demand, never in CI, because
  every score is a paid, slightly variable LLM call. The recorded run
  against `gpt-4.1-nano` reads faithfulness **1.00** on clean answers and
  **0.48** once three fabricated claims are appended to each one
  (recorded 2026-09-04 — the negative control that proves the judge can
  actually fail something; see
  [reference/ragas.md §6](../reference/ragas.md)).

Being crisp about this boundary — what a free deterministic check covers,
and what needs a paid nondeterministic judge instead — is the strongest
answer in this chapter.

## 5. Questions you might get

**"How do you know the assistant gives correct answers?"** — Three layers:
retrieval is *measured* (recall@5 = 1.00 on the golden set — the right
evidence reaches the model); answers are *grounded* (the model cites
retrieved chunks the user can inspect); behavior is *pinned* by 573
deterministic tests. Model-quality evaluation on real models reuses the
same golden harness the day a key exists — which, as of 2026-09-04, it does
for faithfulness (§4).

**"How do you debug a weird answer in production?"** — Open the trace: the
exact prompt, retrieved chunks, tool calls with arguments, token counts,
and latency of every step, in Logfire/Langfuse. That's the point of
instrumenting the agent loop rather than just HTTP.

**"How do you track cost?"** — Token counts ride on the LLM spans; Langfuse
aggregates cost per session/model. Plus the structural safeguards: bounded
prompts (chapter 07) and bounded loops (chapter 04).

**"Why both Logfire and Langfuse?"** — One pipeline, two lenses: Logfire
answers "is the *service* healthy" (latency, errors, spans), Langfuse
answers "what did the *model* do and what did it cost". Both are OTel-based,
so the marginal cost of the second is an exporter, not a second
instrumentation. The alternative
[project/tech-stack.md](../project/tech-stack.md) actually weighs is picking
just one — its own honest caveat is that two dashboards cost attention, and
"Logfire alone is sufficient for the demo" — but both stay wired up anyway
because "one OTel instrumentation, two specialized backends" is itself the
stronger story once the marginal cost is just an exporter.

## 6. Reading it honestly

- **Tracing tells you what happened, not whether it was good.** A perfectly
  traced turn can still be a wrong answer; §4 is exactly the boundary where
  a judge is needed instead of a span.
- **The judge shares a weakness with what it judges.** The Ragas run here
  uses the same model that answered — self-preference is a real, named
  failure mode ([reference/ragas.md §8](../reference/ragas.md)). The 0.48
  control score is evidence the judge catches gross fabrication; it is not
  evidence against subtler, self-serving bias.
- **573 tests at $0 prove behavior, not intelligence.** Every fake is
  scripted to be predictable — a model that got measurably dumber would not
  fail a single one of them. That is exactly why §3's retrieval numbers and
  §4's judge exist as a separate, real-model-dependent tier.
- **The noise sampler is tuned for this deployment's noise, not noise in
  general.** Dropping a root span at sampling time means it never reaches
  Jaeger or the cloud backends at all — correct here, but a sampler built
  for one deployment's "machinery" is a liability if copied into another
  without checking what counts as noise there.

## 7. Related

- [handbook/07 — Observability](../handbook/07-observability.md) — every dashboard, log event, metric and PromQL query, hands-on
- [handbook/09 — Testing & operations](../handbook/09-testing-operations.md) — the full suite map and ops commands
- [reference/ragas.md](../reference/ragas.md) — the LLM judge in full: how it runs, the negative control, reading the number honestly
- [reference/metrics.md](../reference/metrics.md) — recall@k, MRR and the ablation table behind §3's numbers
- [03 — RAG](03-rag.md) — what recall@k and MRR actually measure, and where chunking and reranking fit
