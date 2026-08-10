# 09 — Observability & evals: trusting a nondeterministic system

## Why LLM apps need more than normal logging

Three properties make LLM systems uniquely opaque:

1. **Nondeterminism** — the same input can produce different outputs; "works
   on my machine" means even less than usual.
2. **Cost is per token** — a prompt-building bug (say, resending unsummarized
   history) silently multiplies your bill without a single error log.
3. **Quality regressions are silent** — retrieval getting 10% worse throws
   no exception. You only see it if you *measure*.

The answer is two disciplines: **tracing** (see what actually happened) and
**evals** (measure whether it's any good).

## Tracing with OpenTelemetry

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

## The testing ladder (how you test nondeterminism: you remove it)

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
   embedder), so the numbers are reproducible to the digit:
   0.83 / 1.00 / 0.92 for the default config.
4. **Browser E2E** — Playwright drives the real UI against the real server
   per phase (tool cards render, backends switch, toasts fire).

264 tests, ~22 seconds, fully offline, $0. The fakes aren't a compromise —
they're what makes the suite *possible*.

## What we deliberately do NOT test with fakes

Model *quality* — whether Llama picks the right tool or writes a good
answer. That's what evals against real models are for (the same golden-set
harness, run with a real key), and what tracing monitors in production.
Being crisp about this boundary is the strongest answer in this chapter.

## Questions you might get

**"How do you know the assistant gives correct answers?"** — Three layers:
retrieval is *measured* (recall@5 = 1.00 on the golden set — the right
evidence reaches the model); answers are *grounded* (the model cites
retrieved chunks the user can inspect); behavior is *pinned* by 72
deterministic tests. Model-quality evaluation on real models reuses the
same golden harness the day a key exists.

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
instrumentation.
