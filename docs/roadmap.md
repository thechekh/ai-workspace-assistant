# Roadmap — read the whole project in order

**One path through everything: every document, every source file, and what
to run at each step, sequenced so that each stage assumes only the ones
before it — from "what is this?" to defending it in a room.** The index
([README.md](README.md)) answers *where is X*; this page answers *what do I
read next*. Twelve sessions, roughly 12–14 hours; you can stop after
session 5 and still defend the core. Line counts measured 2026-09-05.

## 1. How to use it

Each session names the documents to read, the source files to read with
them, one command to run, and a self-check. Read the docs first, then the
code with the docs open, then *run the command* — the command is what turns
reading into understanding. Finish by answering the self-check out loud; if
you cannot, the answer is in the file listed.

Order matters: every session assumes the ones before it. Session 4 (RAG) is
the biggest and the one most likely to be probed. §3 is the machine-checked
list of every document in the repository, in reading order — a test fails
the build if a page is ever left out of it.

## 2. The path — twelve sessions

### Session 0 — Orient, then get it running (45 min)

Nothing else makes sense until you have seen it work.

**Read:** the repository [README](../README.md) (what it is, headline
numbers) → the [documentation index](README.md) (how the pages are
organised) → [project/description.md](project/description.md) (the brief,
as received — what was asked for) → [handbook/01 — Project overview](handbook/01-project-overview.md)
(the architecture and one message end to end).

```sh
uv sync                                        # backend deps
cd frontend && npm install && npm run build && cd ..
```

Put `ASSISTANT_REDIS_URL=fakeredis://` in `.env` and leave the provider as
`fake`, then:

```sh
uv run uvicorn assistant.main:app --reload
```

Open http://localhost:8000, ask *"Which service generates invoices?"*, and
watch the answer stream in. Then open http://localhost:8000/dev and send the
same message — that page shows the **raw WebSocket frames**, the best
possible introduction to the protocol.

**Then read:** [handbook/02 — Getting started](handbook/02-getting-started.md)
(every run mode and every `.env` variable), [handbook/README](handbook/README.md)
(the chapter map), and [reference/localhost.md](reference/localhost.md)
(every URL once it runs). Keep [reference/testing.md](reference/testing.md)
open: its Tier A checklist is the click-through for this session.

**You understand this when you can answer:**
- Why does it work with no API key and no Docker? *(the `fake` provider and `fakeredis://`)*
- What are the four run modes, and what does each one add?
- What did the brief ask for that this project deliberately did not build? *(the answer is a page in session 10)*

---

### Session 1 — The shape of the system (60 min)

Before any detail: how the pieces are assembled.

| Read | Lines |
|---|---|
| [`config.py`](../src/assistant/config.py) | 175 |
| [`main.py`](../src/assistant/main.py) | 253 |
| [`agent/base.py`](../src/assistant/agent/base.py) | 76 |
| [`agent/registry.py`](../src/assistant/agent/registry.py) | 28 |

**Read:** [handbook/03 — Technologies](handbook/03-technologies.md) — every
technology, what it does, why it was chosen, where it lives.

```sh
curl -s localhost:8000/api/info | python -m json.tool
```

**You understand this when you can answer:**
- What does `build_runtime` assemble, and why is it separate from `create_app`?
- Why is `app` built lazily in `__getattr__`? *(importing must not read `.env`)*
- What is the `AgentBackend` protocol, and what does it make possible?

---

### Session 2 — The protocol and one turn (90 min)

| Read | Lines |
|---|---|
| [`api/schemas.py`](../src/assistant/api/schemas.py) | 165 |
| [`api/ws.py`](../src/assistant/api/ws.py) | 306 |
| [`api/turn_recorder.py`](../src/assistant/api/turn_recorder.py) | 124 |

**Read:** [theory/README](theory/README.md) (the course map, and the
big-picture diagram of one request) → [theory/01 — LLM basics](theory/01-llm-basics.md)
(tokens, the context window, why the API is stateless) →
[theory/08 — Real-time & WebSockets](theory/08-realtime-websockets.md) →
[handbook/08 — Agents, memory & WebSocket](handbook/08-agents-memory-ws.md)
§2 (the frame protocol and stopping a turn) →
[reference/code-walkthrough.md](reference/code-walkthrough.md) steps 1–4 and
11–12.

```sh
uv run pytest tests/test_ws.py -v      # the whole protocol in one file
```

Then in the UI: start a long answer and press **Stop**. Watch what the stats
line says afterwards.

**You understand this when you can answer:**
- Why does each turn run as an `asyncio.Task` instead of being awaited inline?
- What are the three ways a turn can end, and what do they have in common?
- Why does `TurnRecorder` touch neither the socket nor Redis?

---

### Session 3 — Models, tokens and the provider layer (90 min)

| Read | Lines |
|---|---|
| [`llm/client.py`](../src/assistant/llm/client.py) | 482 — the biggest file; take it in three passes |
| [`llm/errors.py`](../src/assistant/llm/errors.py) | 75 |
| [`llm/fake.py`](../src/assistant/llm/fake.py) | 93 |
| [`telemetry.py`](../src/assistant/telemetry.py) | 183 |

Three passes over `client.py`: (1) `stream_step` — the happy path; (2)
`_create_stream` — 429 backoff and `stream_options` fallback; (3)
`_LeakedTextBuffer` + `parse_leaked_tool_calls` — recovering tool calls a model
printed as prose.

**Read:** [handbook/04 — LLM, models, tokens & cost](handbook/04-llm-models-tokens.md).

```sh
uv run pytest tests/test_llm_errors.py -v
```

**You understand this when you can answer:**
- How does one class serve OpenAI, Ollama and Gemini?
- What three provider failures are handled, and why can a fake never test them?
- When is `usage_estimated` true, and why does it matter for cost?

---

### Session 4 — RAG, the biggest area (2 hours)

Read in **pipeline order** — this is how a document becomes an answer.

| Read | Lines | Stage |
|---|---|---|
| [`rag/chunking.py`](../src/assistant/rag/chunking.py) | 116 | split documents |
| [`rag/embeddings.py`](../src/assistant/rag/embeddings.py) | 110 | text → vectors |
| [`rag/sparse.py`](../src/assistant/rag/sparse.py) | 53 | the keyword channel |
| [`rag/store.py`](../src/assistant/rag/store.py) | 184 | Qdrant + RRF fusion |
| [`rag/rerank.py`](../src/assistant/rag/rerank.py) | 64 | reorder + the relevance gate |
| [`rag/retriever.py`](../src/assistant/rag/retriever.py) | 88 | the orchestrator |
| [`rag/ingest.py`](../src/assistant/rag/ingest.py) | 134 | the write path |
| [`rag/repo.py`](../src/assistant/rag/repo.py) | 240 | GitHub repo → knowledge base, sources namespaced `owner/repo/path` |

**Read:** [theory/02 — Embeddings & vector search](theory/02-embeddings-and-vector-search.md)
→ [theory/03 — RAG](theory/03-rag.md) →
[handbook/05 — RAG & Qdrant](handbook/05-rag-qdrant.md) →
[reference/metrics.md](reference/metrics.md) (recall@k, MRR, the re-measured
table) → [reference/ragas.md](reference/ragas.md) (the judge, and the
control that proves it).

```sh
ASSISTANT_EMBEDDING_PROVIDER=hash uv run python evals/run_retrieval.py --memory              # recall@k + MRR
ASSISTANT_EMBEDDING_PROVIDER=hash uv run python evals/run_retrieval.py --memory --mode dense --no-rerank
```

Run both and compare. The difference between them *is* the argument for the
pipeline.

**You understand this when you can answer:**
- What are dense and sparse vectors doing, and why fuse them?
- What does the reranker earn, in numbers? What does sparse earn?
- What is the relevance gate for? *(so "nothing found" is trustworthy)*
- Why does re-uploading a document delete its old chunks first?
- Why can retrieval quality gate CI when faithfulness cannot?

---

### Session 5 — Agents and tools (90 min)

| Read | Lines |
|---|---|
| [`agent/tools/base.py`](../src/assistant/agent/tools/base.py) | 120 — **the single most important file for safety** |
| [`agent/tools/search_docs.py`](../src/assistant/agent/tools/search_docs.py) | 154 |
| [`agent/tools/fetch.py`](../src/assistant/agent/tools/fetch.py) | 173 |
| [`agent/tools/repo_read.py`](../src/assistant/agent/tools/repo_read.py) | 80 — one exact file from any GitHub repo, tokenless for public |
| [`agent/tools/ingest_repo.py`](../src/assistant/agent/tools/ingest_repo.py) | 105 — the one write tool: adds a repo's docs, nothing else |
| [`agent/output_guard.py`](../src/assistant/agent/output_guard.py) | 71 — why a prompt rule is not a control |
| [`agent/backends/custom.py`](../src/assistant/agent/backends/custom.py) | 98 — the ReAct loop, no framework |

**Read:** [theory/04 — Tool calling & agents](theory/04-tool-calling-and-agents.md)
→ [handbook/06 — Tools & MCP](handbook/06-tools-mcp.md) §1–§2 →
[reference/tools.md](reference/tools.md) (every tool, and the guards shown
failing on purpose).

```sh
uv run pytest tests/test_tool_loop.py tests/test_agent.py -v
```

**You understand this when you can answer:**
- Walk through `CustomAgent.run` in your own words. Where does it stop?
- What does `Tool.run` guarantee, and why is one seam better than four?
- How does a tool crash *not* end the turn?

---

### Session 6 — The two frameworks (60 min)

Only after the hand-written loop makes sense.

| Read | Lines |
|---|---|
| [`agent/backends/pydantic_ai.py`](../src/assistant/agent/backends/pydantic_ai.py) | 286 |
| [`agent/backends/langgraph.py`](../src/assistant/agent/backends/langgraph.py) | 278 |

**Read:** [theory/05 — Agent frameworks](theory/05-agent-frameworks.md) →
[reference/backend-comparison.md](reference/backend-comparison.md) (the same
question on all three, measured) → [handbook/08](handbook/08-agents-memory-ws.md)
§1 (the contract).

```sh
uv run pytest tests/test_fake_parity.py -v     # all three route alike
```

**You understand this when you can answer:**
- What did each framework cost to adopt, in lines and in constraints?
- Why does the pydantic-ai backend re-implement the provider retries?
- What is the strongest argument *for* each of the three?

---

### Session 7 — MCP (45 min)

| Read | Lines |
|---|---|
| [`mcp/registry.py`](../src/assistant/mcp/registry.py) | 110 |
| [`mcp_servers/code_search.py`](../src/assistant/mcp_servers/code_search.py) | 94 |
| [`mcp_servers/fake_github.py`](../src/assistant/mcp_servers/fake_github.py) | 131 |

**Read:** [theory/06 — MCP](theory/06-mcp.md) →
[handbook/06 — Tools & MCP](handbook/06-tools-mcp.md) §3–§5.

```sh
uv run pytest tests/test_mcp.py -v      # spawns the real servers
```

**You understand this when you can answer:**
- What problem does MCP solve that a plain SDK does not?
- What is tool namespacing for?
- What happens when an MCP server is unreachable, and why that choice?

---

### Session 8 — Memory (45 min)

| Read | Lines |
|---|---|
| [`memory/session.py`](../src/assistant/memory/session.py) | 153 |
| [`memory/conversation.py`](../src/assistant/memory/conversation.py) | 53 — small and important |
| [`memory/summarizer.py`](../src/assistant/memory/summarizer.py) | 63 |

**Read:** [theory/07 — Conversation memory](theory/07-memory.md) →
[handbook/08](handbook/08-agents-memory-ws.md) §3–§5.

```sh
uv run pytest tests/test_memory.py -v
```

**You understand this when you can answer:**
- Why does the model not see the full transcript?
- What stops the prompt growing forever, and how is that proven?
- Where is long-term memory in this system? *(the vector DB)*

---

### Session 9 — Observability, security and operations (2 hours)

| Read | Lines |
|---|---|
| [`telemetry.py`](../src/assistant/telemetry.py) | 183 — revisit with fresh eyes |
| [`observability.py`](../src/assistant/observability.py) | 178 |
| [`logs.py`](../src/assistant/logs.py) | 56 |
| [`api/routes.py`](../src/assistant/api/routes.py) | 313 |
| [`api/rate_limit.py`](../src/assistant/api/rate_limit.py) | 87 |

**Read:** [theory/09 — Observability & evals](theory/09-observability-and-evals.md)
→ [theory/10 — Infrastructure](theory/10-infrastructure.md) →
[handbook/07 — Observability](handbook/07-observability.md) →
[reference/logfire-langfuse.md](reference/logfire-langfuse.md) (the two cloud
lenses, verified) → [handbook/09 — Testing & operations](handbook/09-testing-operations.md)
→ [reference/security.md](reference/security.md) (every control, shown
refusing).

```sh
docker compose --profile observability up -d
# set ASSISTANT_OTLP_ENDPOINT=http://localhost:4318 and restart the server
```

Send one message, then look at all four views: the log lines, `/metrics`,
Jaeger (:16686) and Grafana (:3000).

**You understand this when you can answer:**
- Name the four spans and what each wraps.
- Why is tracing inert by default, and what does that buy?
- What is the rate limiter protecting, and why a sliding window?
- What can the model *not* do, and which test proves it?

---

### Session 10 — The frontend (60 min)

| Read | Lines |
|---|---|
| [`frontend/src/types.ts`](../frontend/src/types.ts) | 108 — the protocol, mirrored |
| [`frontend/src/stores/chat.ts`](../frontend/src/stores/chat.ts) | 462 — the WS reducer |
| [`frontend/src/App.vue`](../frontend/src/App.vue) | 52 |

Then the components: `ChatWindow`, `ChatMessage`, `ChatInput`, `ToolCard`,
`ModeToggle`, `SessionsPanel`, `DocumentsPanel`.

```sh
cd frontend && npm run test:run
```

**You understand this when you can answer:**
- How does a `token` frame become text on screen?
- What does Dev mode change, and what does it deliberately *not* change?
- Why does the store fetch a transcript over HTTP when reopening a chat?

---

### Session 11 — How it is all proven, and why it looks like this (90 min)

The part most projects cannot show, then the decisions behind it.

- **The map:** [handbook/09](handbook/09-testing-operations.md) — every test
  file and what it proves
- **Start with** [`tests/test_ws.py`](../tests/test_ws.py) — the protocol,
  parametrized over all three backends
- **Then** [`tests/test_review_regressions.py`](../tests/test_review_regressions.py)
  — bugs a full review found, each reproduced before it was fixed
- **The evals:** [`evals/run_retrieval.py`](../evals/run_retrieval.py) (gated
  in CI) and [`evals/run_ragas.py`](../evals/run_ragas.py) (groundedness, on
  demand)
- **The whole system in execution order:** [reference/code-walkthrough.md](reference/code-walkthrough.md),
  now in full — one real turn through every layer, file and line by line

```sh
uv run pytest -q                                    # all of it, offline
ASSISTANT_EMBEDDING_PROVIDER=hash uv run python evals/run_retrieval.py --memory --check
```

**Then the decisions:** [project/tech-stack.md](project/tech-stack.md)
(every choice and the alternatives that lost) →
[project/implementation-plan.md](project/implementation-plan.md) (what
shipped, phase by phase, with the evidence) →
[project/future-tools.md](project/future-tools.md) (what was evaluated and
deliberately not built, priced) →
[project/description-original.md](project/description-original.md) (the
brief in its original language — compare with what shipped).

**You understand this when you can answer:**
- Why can the retrieval eval gate CI while the groundedness eval cannot?
- What does `test_fake_parity.py` prove, and what can it *not* see?
- How do the docs stay true? *(four test files check links, facts, coverage and shape)*
- Name three things the brief asked for that were cut, and the trigger that would bring each back.

---

### Session 12 — Rehearsal (90 min)

- [theory/11 — Glossary](theory/11-glossary.md) — cover the definition and say it
- [theory/12 — Defense Q&A](theory/12-defense-qa.md) — answer each out loud, without notes
- [qanda/README.md](qanda/README.md) — 69 questions in the same shape, 21 of them about this codebase specifically
- [project/workshop.md](project/workshop.md) — the slide outline and the demo script, click by click
- [project/demo-runbook.md](project/demo-runbook.md) — the real stack: keys, infrastructure, the three deliverable queries and their cost
- Run the demo end to end **twice**, once with the network off

If you will change the repository afterwards: [CLAUDE.md](../CLAUDE.md)
(what coding agents read first) and
[project/documentation-standard.md](project/documentation-standard.md) (how
every page here is written, and which rules the tests enforce).

## 3. Every document, placed

The machine-checked list: every Markdown file in the repository, once, in
the order the sessions above read them. `tests/test_docs_coverage.py`
fails the build if a page is missing from this table or the numbering
skips. Tick them off.

| # | Document | Answers |
|---|---|---|
| 1 | [../README.md](../README.md) | The landing page: what it is, quickstart, headline numbers |
| 2 | [README.md](README.md) | How the documentation is organised, and where to go for what |
| 3 | [roadmap.md](roadmap.md) *(this page)* | The path through all of it |
| 4 | [project/description.md](project/description.md) | The brief — what was actually asked for |
| 5 | [handbook/01-project-overview.md](handbook/01-project-overview.md) | Architecture diagram + one message end to end |
| 6 | [handbook/02-getting-started.md](handbook/02-getting-started.md) | Every run mode and every `.env` variable |
| 7 | [handbook/README.md](handbook/README.md) | The chapter map, and every localhost URL in one place |
| 8 | [reference/localhost.md](reference/localhost.md) | Every localhost link once it runs: dashboards, logs, the vector DB |
| 9 | [reference/testing.md](reference/testing.md) | The manual checklist, tiered from zero-infra to the real model |
| 10 | [handbook/03-technologies.md](handbook/03-technologies.md) | Every technology: what it does, why chosen, where it lives |
| 11 | [theory/README.md](theory/README.md) | The course map |
| 12 | [theory/01-llm-basics.md](theory/01-llm-basics.md) | Tokens, context, streaming, hallucination |
| 13 | [theory/08-realtime-websockets.md](theory/08-realtime-websockets.md) | Streaming, WebSocket vs SSE, cancellation |
| 14 | [handbook/04-llm-models-tokens.md](handbook/04-llm-models-tokens.md) | Providers, retries, usage, cost, limits |
| 15 | [theory/02-embeddings-and-vector-search.md](theory/02-embeddings-and-vector-search.md) | Vectors, similarity, dense vs sparse, HNSW |
| 16 | [theory/03-rag.md](theory/03-rag.md) | Chunking, retrieval, grounding, recall@k, MRR |
| 17 | [handbook/05-rag-qdrant.md](handbook/05-rag-qdrant.md) | The full pipeline + measured quality |
| 18 | [reference/metrics.md](reference/metrics.md) | recall@k, MRR, groundedness — re-measured, and what each one hides |
| 19 | [reference/ragas.md](reference/ragas.md) | The LLM judge: what Ragas is, how to run and read it, the control that proves it |
| 20 | [theory/04-tool-calling-and-agents.md](theory/04-tool-calling-and-agents.md) | Function calling, the ReAct loop |
| 21 | [handbook/06-tools-mcp.md](handbook/06-tools-mcp.md) | The tool inventory and the execution seam |
| 22 | [reference/tools.md](reference/tools.md) | Every tool: parameters, returns, errors, and the guards shown failing |
| 23 | [theory/05-agent-frameworks.md](theory/05-agent-frameworks.md) | Pydantic AI, LangGraph, and why write a loop yourself |
| 24 | [reference/backend-comparison.md](reference/backend-comparison.md) | custom vs Pydantic AI vs LangGraph, measured |
| 25 | [handbook/08-agents-memory-ws.md](handbook/08-agents-memory-ws.md) | The contract, the frame protocol, summarization, the output guard |
| 26 | [theory/06-mcp.md](theory/06-mcp.md) | The protocol, transports, namespacing |
| 27 | [theory/07-memory.md](theory/07-memory.md) | Short vs long term, rolling summaries |
| 28 | [theory/09-observability-and-evals.md](theory/09-observability-and-evals.md) | Traces, metrics, and measuring quality |
| 29 | [theory/10-infrastructure.md](theory/10-infrastructure.md) | Redis, Qdrant, Docker, the toolchain, auth |
| 30 | [handbook/07-observability.md](handbook/07-observability.md) | Logs, metrics, traces, health, audit — with captures |
| 31 | [reference/logfire-langfuse.md](reference/logfire-langfuse.md) | The two cloud lenses: purpose, comparison, wiring, what each shows |
| 32 | [handbook/09-testing-operations.md](handbook/09-testing-operations.md) | The suite map, ops, troubleshooting |
| 33 | [reference/security.md](reference/security.md) | Threat model, every control shown refusing, what is deliberately absent |
| 34 | [reference/code-walkthrough.md](reference/code-walkthrough.md) | One real turn through every layer, file and line by line |
| 35 | [project/tech-stack.md](project/tech-stack.md) | Every technology decision and the alternatives rejected |
| 36 | [project/implementation-plan.md](project/implementation-plan.md) | Phase-by-phase build history with acceptance evidence |
| 37 | [project/future-tools.md](project/future-tools.md) | Every tool considered and deferred: cost, verdict, trigger |
| 38 | [project/description-original.md](project/description-original.md) | The original brief as received — compare with what shipped |
| 39 | [theory/11-glossary.md](theory/11-glossary.md) | Every term in one line, and the terms deliberately not used |
| 40 | [theory/12-defense-qa.md](theory/12-defense-qa.md) | The hard questions, with answers — do this without notes |
| 41 | [qanda/README.md](qanda/README.md) | 69 hard questions, each followed by its grounded answer |
| 42 | [project/workshop.md](project/workshop.md) | Slide outline and the click-by-click demo script |
| 43 | [project/demo-runbook.md](project/demo-runbook.md) | Running the demo on the real stack: keys, infra, costs |
| 44 | [../CLAUDE.md](../CLAUDE.md) | Instructions for AI coding agents working in this repo |
| 45 | [project/documentation-standard.md](project/documentation-standard.md) | How pages are written: the fourteen rules, and which the tests enforce |

## 4. Coverage checklist — the code

Every source file, and the session that covers it. Nothing is left over.

| Area | Files | Session |
|---|---|---|
| Configuration & wiring | `config.py`, `main.py`, `agent/registry.py`, `agent/base.py` | 1 |
| WebSocket & protocol | `api/schemas.py`, `api/ws.py`, `api/turn_recorder.py` | 2 |
| LLM layer | `llm/client.py`, `llm/errors.py`, `llm/fake.py` | 3 |
| RAG | `rag/chunking.py`, `embeddings.py`, `sparse.py`, `store.py`, `rerank.py`, `retriever.py`, `ingest.py`, `repo.py` | 4 |
| Tools & the loop | `agent/tools/base.py`, `search_docs.py`, `fetch.py`, `ingest_repo.py`, `repo_read.py`, `agent/output_guard.py`, `backends/custom.py` | 5 |
| Frameworks | `backends/pydantic_ai.py`, `backends/langgraph.py` | 6 |
| MCP | `mcp/registry.py`, `mcp_servers/code_search.py`, `fake_github.py` | 7 |
| Memory | `memory/session.py`, `conversation.py`, `summarizer.py` | 8 |
| Observability & ops | `telemetry.py`, `observability.py`, `logs.py`, `api/routes.py`, `api/rate_limit.py` | 9 |
| Frontend | `types.ts`, `stores/chat.ts`, `App.vue`, components | 10 |
| Tests & evals | `tests/` (26 files), `evals/` (4 scripts) | 11 |

**38 source files. 12 sessions. No gaps.**

## 5. The short paths

**One evening:** sessions **0, 2, 4, 5** — running it, the protocol, RAG,
and the agent loop. That is the spine, and it is what most questions are
about. Then the [10-minute version](reference/code-walkthrough.md) at the
top of the walkthrough, and the [Defense Q&A](theory/12-defense-qa.md).

**Two hours before presenting:** rows **1 → 5 → 6 → 34 → 40** of §3 — what it
is, the architecture, how to run it, the code walkthrough, and the defence
Q&A. **Presenting it?** [project/workshop.md](project/workshop.md) has the
slide outline, the click-by-click demo script, and the file-map walkthrough;
[project/demo-runbook.md](project/demo-runbook.md) has the real stack.

## 6. Reading it honestly

- **Twelve to fourteen hours is a reading estimate, not a measurement.**
  Nobody has timed a stranger through it; the commands and self-checks are
  what make the time count, and they are the part not to skip.
- **The order is one author's.** Theory chapters are interleaved with the
  handbook and the code where they pay off soonest; a reader who already
  knows LLMs can skip session 3's theory and lose nothing, and a reader who
  only wants to run it can stop after session 0.
- **Line counts drift.** They were measured on 2026-09-05 and the build does
  not check them; the coverage test checks that every source file is *named*
  here, not that its size is current.
- **Only the table in §3 is machine-checked.** The session plan above it is
  prose; a new page must be added to both, and the test only notices the
  table.

## 7. Related

- [README.md](README.md) — the index: where to go for a given need, and the by-topic table
- [reference/code-walkthrough.md](reference/code-walkthrough.md) — the same code in execution order instead of session order
- [theory/README.md](theory/README.md) — the concept course this roadmap interleaves with the code
- [handbook/README.md](handbook/README.md) — the operator's chapters this roadmap runs alongside
- [project/workshop.md](project/workshop.md) — the demo script for presenting what this roadmap teaches
- [project/documentation-standard.md](project/documentation-standard.md) — why every page along the way has the same shape
