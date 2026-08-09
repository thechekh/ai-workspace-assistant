# Workshop — AI Workspace Assistant

Materials for the department workshop: slide outline, click-by-click live
demo script, and the implementation walkthrough map. Everything runs
offline with zero API keys (fake LLM + hash embedder); with a Groq key the
same demos run on a real model.

**Presenter prep:** the [theory course](../theory/README.md) explains
every concept from zero (LLMs, embeddings, RAG, agents, MCP, memory…) with
per-chapter Q&A, plus a dedicated [defense Q&A](../theory/12-defense-qa.md)
for the hard questions.

---

## Part 1 — AI architecture (slides outline)

1. **The problem** — engineers ask the same questions about the codebase,
   docs, and systems; the answers exist but are scattered.
2. **RAG** — chunking (heading-aware, breadcrumbs), embeddings, vector
   search. *Our numbers slide:* golden set of 18 questions, measured at zero
   cost (`evals/run_retrieval.py --memory`) and re-checked by CI on every
   push:

   | | recall@1 | recall@5 | MRR |
   |---|---:|---:|---:|
   | dense, no rerank | 0.78 | 0.94 | 0.86 |
   | hybrid (RRF), no rerank | 0.72 | 1.00 | 0.86 |
   | **hybrid + rerank** *(default)* | **0.83** | **1.00** | **0.92** |

   Say the honest version out loud — it is a stronger slide than a tidy
   staircase: **sparse buys recall@5** (every question lands in the top 5),
   **the reranker buys recall@1** (+0.11), and the recall@1 wobble between
   dense and hybrid is a single question out of eighteen. Expect the
   follow-up "then why is hybrid your default?" — the answer is in the
   [defense Q&A](../theory/12-defense-qa.md).
3. **Agentic systems** — the ReAct loop: model → tool call → result → model.
   Show `backends/custom.py` (98 lines, no framework). Loop bounded at 6
   iterations; a tool crash becomes an error *result*, never an exception.
4. **Three runtimes, one contract** — `AgentBackend` protocol; custom vs
   Pydantic AI vs LangGraph, switchable per session. *Slide source:*
   [backend-comparison.md](../reference/backend-comparison.md) (measured LoC
   98/194/278 + verdict table). The same test suite passes on all three.
5. **MCP** — why a protocol beats N bespoke integrations; stdio vs
   streamable HTTP; tool namespacing; graceful degradation. Our servers:
   `code_search` (real) + `fake_github` (mock with the official server's
   tool names — swapping to real GitHub is a config change).
6. **Memory** — short-term Redis history + rolling summarization (context
   stops growing; each message summarized once); long-term = the vector DB.
7. **Observability** — five views of one turn: structured logs with
   correlation IDs, `/metrics`, an OTel trace waterfall
   (`agent.turn → llm.step → tool.execute → rag.retrieve`), per-turn stats
   **and cost** in the UI, and a replayable audit trail. All local, zero
   accounts (Jaeger + Prometheus + Grafana in one compose profile); Logfire
   and Langfuse are optional extra destinations on the same pipeline.
8. **Making real models behave** — what live testing against Groq forced:
   429 backoff honouring `Retry-After`, retry + salvage when llama emits a
   malformed tool call, parsing leaked `<function…>` text back into real
   calls, and a per-turn duplicate-call guard.

## Part 2 — Live demo (script)

Setup beforehand:

```sh
docker compose up -d                                  # redis + qdrant
uv run uvicorn assistant.main:app                     # open http://localhost:8000/
# (no Docker available? ASSISTANT_REDIS_URL=fakeredis:// works too)
# Optional, for the trace waterfall in step 7:
docker compose --profile observability up -d          # Jaeger + Prometheus + Grafana
```

The knowledge base starts **empty** — filling it is demo step 1.

1. **Empty → useful, live** — ask `What is our deployment architecture?`
   *before* adding anything → the assistant says the knowledge base is empty
   and asks for documents. Now open the **Documents** panel, drop
   `evals/corpus/architecture/deployment.md` (or your own docs), and ask the
   **same question again** → the `search_docs` card opens with retrieved
   chunks + scores, and the answer cites the source. This is the strongest
   opening: the system visibly learns in front of the room.
2. **Follow-up with memory** — ask: `How do we roll back?`
   → history flows through Redis; same session.
3. **MCP demo (GitHub)** — ask: `Show latest PRs in the repo`
   → `github__list_pull_requests` card; explain the mock ↔ real swap.
4. **MCP demo (code search)** — ask: `search code for class CustomAgent`
   → `code__search_code` card finds the actual class in this repo.
5. **Backend comparison** — switch the dropdown custom → pydantic-ai →
   langgraph, re-ask question 1: same tools, same answer shape, three
   runtimes (server logs show the reconnect with the same session id).
6. **Explain the answer** — flip the header toggle from **Standard** to
   **Dev**: the tool cards and stats lines appear *for messages already on
   screen*, because the data was there all along. Click **details** under any
   reply for the turn's timeline (tool_call → tool_result → final with `+ms`
   offsets) alongside duration, first-token latency, LLM steps, real token
   counts and cost.
6b. **Stop an answer** — ask for something long ("summarise everything you
   know about our deployment") and press **Stop** (or `Esc`) mid-stream. The
   partial answer stays, marked as stopped, and its stats line still shows
   the tokens that were actually spent. Worth one sentence: this is why the
   protocol is a WebSocket and not SSE, and why each turn runs as its own
   task.
6c. **Reopen a conversation** — the **Chats** panel lists recent sessions;
   pick an earlier one and the transcript comes back, then ask a follow-up
   that depends on it to show the model has the history too.
7. **Observability moment** — Jaeger (`localhost:16686`) → newest
   `agent.turn` trace → the waterfall showing exactly where the time went;
   Grafana (`localhost:3000`) → the provisioned dashboard moving live.
8. **(With keys only)** — flip `.env` to Groq: same demos, real model, real
   token counts and dollar cost per turn.

Fallback: every step above also works with the offline fake LLM — answers
are extractive but the full tool loop is real.

## Part 3 — Implementation walkthrough (file map)

| Area | Where | Talking points |
|---|---|---|
| WS protocol | `api/schemas.py`, `api/ws.py` | typed frames, error frames don't kill the socket, turns as tasks so `cancel` can land mid-stream |
| Throttling | `api/rate_limit.py` | sliding window in Redis; why not `INCR`+`EXPIRE` |
| Agent contract | `agent/base.py` | one protocol, one event stream |
| The loop | `agent/backends/custom.py` | ReAct in ~100 lines |
| Frameworks | `agent/backends/pydantic_ai.py`, `langgraph.py` | adapters, streaming APIs, checkpointer |
| Tools | `agent/tools/` | registry shared by all backends |
| MCP | `mcp/registry.py`, `mcp_servers/` | client + bundled servers |
| RAG | `rag/` | chunking → embeddings → hybrid store → rerank |
| Memory | `memory/` | Redis history + rolling summary |
| Evals | `evals/` | golden set, retrieval metrics, embedding comparison, `baseline.json` as a CI gate |
| Jobs | `worker.py` | taskiq broker + nightly cron |
| Frontend | `frontend/src/` | Pinia store owns the WS; tool cards |

Suggested flow: open the WS test (`tests/test_ws.py`) first — the protocol
in one screen — then walk `custom.py`, then show the same test suite
passing ×3 backends (`uv run pytest -q`).
