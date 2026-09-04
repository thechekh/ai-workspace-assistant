# Learning roadmap — how to explore this project

A sequenced plan for understanding the whole system: every technology, every
source file. Twelve sessions, roughly **12–14 hours** total. You can stop after
session 5 and still defend the core.

**How this differs from the other guides.** The
[theory course](../theory/README.md) explains concepts from zero. The
[handbook](../handbook/README.md) explains how to run and operate it. The
[code walkthrough](../reference/code-walkthrough.md) follows one request
through the code in execution order. **This page sequences all three into a
study plan** — what to read, what to run, and how to know you understood it.

**How to use a session.** Read the docs first, then the code with the docs
open, then *run the command* — the command is what turns reading into
understanding. Finish by answering the self-check out loud. If you cannot, the
answer is in the file listed.

> Order matters. Each session assumes the ones before it. Session 4 (RAG) is
> the biggest and the one most likely to be probed.

---

## Session 0 — Get it running (30 min)

Nothing else makes sense until you have seen it work.

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
same message — that page shows the **raw WebSocket frames**, which is the best
possible introduction to the protocol.

**Read:** [handbook 02 — getting started](../handbook/02-getting-started.md)

**You understand this when you can answer:**
- Why does it work with no API key and no Docker? *(the `fake` provider and `fakeredis://`)*
- What are the three ways to run it? *(zero-infra, Docker Compose, real provider)*

---

## Session 1 — The shape of the system (60 min)

Before any detail: how the pieces are assembled.

| Read | Lines |
|---|---|
| [`config.py`](../../src/assistant/config.py) | 175 |
| [`main.py`](../../src/assistant/main.py) | 253 |
| [`agent/base.py`](../../src/assistant/agent/base.py) | 76 |
| [`agent/registry.py`](../../src/assistant/agent/registry.py) | 28 |

**Read:** [handbook 01 — overview](../handbook/01-project-overview.md) ·
[handbook 03 — technologies](../handbook/03-technologies.md)

```sh
curl -s localhost:8000/api/info | python -m json.tool
```

**You understand this when you can answer:**
- What does `build_runtime` assemble, and why is it separate from `create_app`?
- Why is `app` built lazily in `__getattr__`? *(importing must not read `.env`)*
- What is the `AgentBackend` protocol, and what does it make possible?

---

## Session 2 — The protocol and one turn (90 min)

| Read | Lines |
|---|---|
| [`api/schemas.py`](../../src/assistant/api/schemas.py) | 165 |
| [`api/ws.py`](../../src/assistant/api/ws.py) | 306 |
| [`api/turn_recorder.py`](../../src/assistant/api/turn_recorder.py) | 124 |

**Read:** [theory 08 — WebSockets](../theory/08-realtime-websockets.md) ·
[handbook 08 — agents, memory, WS](../handbook/08-agents-memory-ws.md) ·
[walkthrough steps 1–4 and 11–12](../reference/code-walkthrough.md)

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

## Session 3 — Models, tokens and the provider layer (90 min)

| Read | Lines |
|---|---|
| [`llm/client.py`](../../src/assistant/llm/client.py) | 482 — the biggest file; take it in three passes |
| [`llm/errors.py`](../../src/assistant/llm/errors.py) | 75 |
| [`llm/fake.py`](../../src/assistant/llm/fake.py) | 93 |
| [`telemetry.py`](../../src/assistant/telemetry.py) | 160 |

Three passes over `client.py`: (1) `stream_step` — the happy path; (2)
`_create_stream` — 429 backoff and `stream_options` fallback; (3)
`_LeakedTextBuffer` + `parse_leaked_tool_calls` — recovering tool calls a model
printed as prose.

**Read:** [theory 01 — LLM basics](../theory/01-llm-basics.md) ·
[handbook 04 — models, tokens, cost](../handbook/04-llm-models-tokens.md)

```sh
uv run pytest tests/test_llm_errors.py -v
```

**You understand this when you can answer:**
- How does one class serve OpenAI, Ollama and Gemini?
- What three provider failures are handled, and why can a fake never test them?
- When is `usage_estimated` true, and why does it matter for cost?

---

## Session 4 — RAG, the biggest area (2 hours)

Read in **pipeline order** — this is how a document becomes an answer.

| Read | Lines | Stage |
|---|---|---|
| [`rag/chunking.py`](../../src/assistant/rag/chunking.py) | 116 | split documents |
| [`rag/embeddings.py`](../../src/assistant/rag/embeddings.py) | 110 | text → vectors |
| [`rag/sparse.py`](../../src/assistant/rag/sparse.py) | 53 | the keyword channel |
| [`rag/store.py`](../../src/assistant/rag/store.py) | 184 | Qdrant + RRF fusion |
| [`rag/rerank.py`](../../src/assistant/rag/rerank.py) | 64 | reorder + the relevance gate |
| [`rag/retriever.py`](../../src/assistant/rag/retriever.py) | 88 | the orchestrator |
| [`rag/ingest.py`](../../src/assistant/rag/ingest.py) | 134 | the write path |
| [`rag/repo.py`](../../src/assistant/rag/repo.py) | 240 | GitHub repo → knowledge base, sources namespaced `owner/repo/path` |

**Read:** [theory 02 — embeddings](../theory/02-embeddings-and-vector-search.md) ·
[theory 03 — RAG](../theory/03-rag.md) ·
[handbook 05 — RAG & Qdrant](../handbook/05-rag-qdrant.md) ·
[metrics.md](../reference/metrics.md)

```sh
uv run python evals/run_retrieval.py --memory              # recall@k + MRR
uv run python evals/run_retrieval.py --memory --mode dense --no-rerank
```

Run both and compare. The difference between them *is* the argument for the
pipeline.

**You understand this when you can answer:**
- What are dense and sparse vectors doing, and why fuse them?
- What does the reranker earn, in numbers? What does sparse earn?
- What is the relevance gate for? *(so "nothing found" is trustworthy)*
- Why does re-uploading a document delete its old chunks first?

---

## Session 5 — Agents and tools (90 min)

| Read | Lines |
|---|---|
| [`agent/tools/base.py`](../../src/assistant/agent/tools/base.py) | 120 — **the single most important file for safety** |
| [`agent/tools/search_docs.py`](../../src/assistant/agent/tools/search_docs.py) | 154 |
| [`agent/tools/fetch.py`](../../src/assistant/agent/tools/fetch.py) | 173 |
| [`agent/tools/repo_read.py`](../../src/assistant/agent/tools/repo_read.py) | 80 — one exact file from any GitHub repo, tokenless for public |
| [`agent/tools/ingest_repo.py`](../../src/assistant/agent/tools/ingest_repo.py) | 105 — the one write tool: adds a repo's docs, nothing else |
| [`agent/output_guard.py`](../../src/assistant/agent/output_guard.py) | 71 — why a prompt rule is not a control |
| [`agent/backends/custom.py`](../../src/assistant/agent/backends/custom.py) | 98 — the ReAct loop, no framework |

**Read:** [theory 04 — tool calling & agents](../theory/04-tool-calling-and-agents.md) ·
[reference/tools.md](../reference/tools.md)

```sh
uv run pytest tests/test_tool_loop.py tests/test_agent.py -v
```

**You understand this when you can answer:**
- Walk through `CustomAgent.run` in your own words. Where does it stop?
- What does `Tool.run` guarantee, and why is one seam better than four?
- How does a tool crash *not* end the turn?

---

## Session 6 — The two frameworks (60 min)

Only after the hand-written loop makes sense.

| Read | Lines |
|---|---|
| [`agent/backends/pydantic_ai.py`](../../src/assistant/agent/backends/pydantic_ai.py) | 286 |
| [`agent/backends/langgraph.py`](../../src/assistant/agent/backends/langgraph.py) | 278 |

**Read:** [theory 05 — agent frameworks](../theory/05-agent-frameworks.md) ·
[backend-comparison.md](../reference/backend-comparison.md)

```sh
uv run pytest tests/test_fake_parity.py -v     # all three route alike
```

**You understand this when you can answer:**
- What did each framework cost to adopt, in lines and in constraints?
- Why does the pydantic-ai backend re-implement the provider retries?
- What is the strongest argument *for* each of the three?

---

## Session 7 — MCP (45 min)

| Read | Lines |
|---|---|
| [`mcp/registry.py`](../../src/assistant/mcp/registry.py) | 110 |
| [`mcp_servers/code_search.py`](../../src/assistant/mcp_servers/code_search.py) | 94 |
| [`mcp_servers/fake_github.py`](../../src/assistant/mcp_servers/fake_github.py) | 131 |

**Read:** [theory 06 — MCP](../theory/06-mcp.md) ·
[handbook 06 — tools & MCP](../handbook/06-tools-mcp.md)

```sh
uv run pytest tests/test_mcp.py -v      # spawns the real servers
```

**You understand this when you can answer:**
- What problem does MCP solve that a plain SDK does not?
- What is tool namespacing for?
- What happens when an MCP server is unreachable, and why that choice?

---

## Session 8 — Memory (45 min)

| Read | Lines |
|---|---|
| [`memory/session.py`](../../src/assistant/memory/session.py) | 153 |
| [`memory/conversation.py`](../../src/assistant/memory/conversation.py) | 53 — small and important |
| [`memory/summarizer.py`](../../src/assistant/memory/summarizer.py) | 63 |

**Read:** [theory 07 — memory](../theory/07-memory.md)

```sh
uv run pytest tests/test_memory.py -v
```

**You understand this when you can answer:**
- Why does the model not see the full transcript?
- What stops the prompt growing forever, and how is that proven?
- Where is long-term memory in this system? *(the vector DB)*

---

## Session 9 — Observability and operations (90 min)

| Read | Lines |
|---|---|
| [`telemetry.py`](../../src/assistant/telemetry.py) | 160 — revisit with fresh eyes |
| [`observability.py`](../../src/assistant/observability.py) | 92 |
| [`logs.py`](../../src/assistant/logs.py) | 56 |
| [`api/routes.py`](../../src/assistant/api/routes.py) | 313 |
| [`api/rate_limit.py`](../../src/assistant/api/rate_limit.py) | 87 |

**Read:** [theory 09 — observability & evals](../theory/09-observability-and-evals.md) ·
[handbook 07](../handbook/07-observability.md) ·
[handbook 09](../handbook/09-testing-operations.md) ·
[security.md](../reference/security.md)

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

---

## Session 10 — The frontend (60 min)

| Read | Lines |
|---|---|
| [`frontend/src/types.ts`](../../frontend/src/types.ts) | 108 — the protocol, mirrored |
| [`frontend/src/stores/chat.ts`](../../frontend/src/stores/chat.ts) | 477 — the WS reducer |
| [`frontend/src/App.vue`](../../frontend/src/App.vue) | 59 |

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

## Session 11 — How it is all proven (60 min)

The part most projects cannot show.

- **The map:** [handbook 09](../handbook/09-testing-operations.md) — every test
  file and what it proves
- **Start with** [`tests/test_ws.py`](../../tests/test_ws.py) — the protocol,
  parametrized over all three backends
- **Then** [`tests/test_review_regressions.py`](../../tests/test_review_regressions.py)
  — bugs a full review found, each reproduced before it was fixed
- **The evals:** [`evals/run_retrieval.py`](../../evals/run_retrieval.py) (gated
  in CI) and [`evals/run_ragas.py`](../../evals/run_ragas.py) (groundedness, on
  demand)

```sh
uv run pytest -q                                    # all of it, offline
uv run python evals/run_retrieval.py --memory --check
```

**You understand this when you can answer:**
- Why can the retrieval eval gate CI while the groundedness eval cannot?
- What does `test_fake_parity.py` prove, and what can it *not* see?
- How do the docs stay true? *(three test files check links, facts, coverage)*

---

## Session 12 — Rehearsal (60 min)

- [defense Q&A](../theory/12-defense-qa.md) — answer each out loud, without notes
- [glossary](../theory/11-glossary.md) — 119 terms; cover the definition and say it
- [workshop.md](workshop.md) — the demo script, click by click
- Run the demo end to end **twice**, once with the network off

---

## Coverage checklist

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
| Tests & evals | `tests/` (25 files), `evals/` (4 scripts) | 11 |

**38 source files. 12 sessions. No gaps.**

---

## If you only have one evening

Sessions **0, 2, 4, 5** — running it, the protocol, RAG, and the agent loop.
That is the spine, and it is what most questions are about. Then read the
[10-minute version](../reference/code-walkthrough.md#the-10-minute-version) of
the walkthrough and the [defense Q&A](../theory/12-defense-qa.md).
