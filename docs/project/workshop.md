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
   search. *Our numbers slide:* golden set of 18 questions; dense baseline
   recall@1 0.56 → hybrid (dense + sparse RRF) 0.67 → + lexical rerank
   **0.83**, recall@5 **1.00** — measured, zero cost
   (`evals/run_retrieval.py --memory`).
3. **Agentic systems** — the ReAct loop: model → tool call → result → model.
   Show `backends/custom.py` (103 lines, no framework). Loop bounds, error
   results instead of exceptions.
4. **Three runtimes, one contract** — `AgentBackend` protocol; custom vs
   Pydantic AI vs LangGraph, switchable per session. *Slide source:*
   `docs/backend-comparison.md` (measured LoC 103/209/282 + verdict table).
5. **MCP** — why a protocol beats N bespoke integrations; stdio vs
   streamable HTTP; tool namespacing; graceful degradation. Our servers:
   `code_search` (real) + `fake_github` (mock with the official server's
   tool names — swapping to real GitHub is a config change).
6. **Memory** — short-term Redis history + rolling summarization (context
   stops growing; each message summarized once); long-term = the vector DB.
7. **Observability** — one OTel pipeline → Logfire (app view) + Langfuse
   (LLM view); inert without tokens.

## Part 2 — Live demo (script)

Setup beforehand:

```sh
docker compose up -d                                  # redis + qdrant
uv run python -m assistant.rag.ingest docs_corpus --recreate
uv run uvicorn assistant.main:app                     # open http://localhost:8000/
# (no Docker available? ASSISTANT_REDIS_URL=fakeredis:// works too)
```

1. **RAG demo** — ask: `What is our deployment architecture?`
   → the `search_docs` tool card opens with retrieved chunks + scores from
   `architecture/deployment.md`; the answer cites the sources.
2. **Follow-up with memory** — ask: `How do we roll back?`
   → history flows through Redis; same session.
3. **MCP demo (GitHub)** — ask: `Show latest PRs in the repo`
   → `github__list_pull_requests` card; explain the mock ↔ real swap.
4. **MCP demo (code search)** — ask: `search code for class CustomAgent`
   → `code__search_code` card finds the actual class in this repo.
5. **Backend comparison** — switch the dropdown custom → pydantic-ai →
   langgraph, re-ask question 1: same tools, same answer shape, three
   runtimes (server logs show the reconnect with the same session id).
6. **Ops moment** — click **Re-index** (taskiq queued job — or inline in
   zero-infra mode); show the toast.
7. **(With keys only)** — flip `.env` to Groq: same demos, real model;
   Logfire/Langfuse traces if tokens are set.

Fallback: every step above also works with the offline fake LLM — answers
are extractive but the full tool loop is real.

## Part 3 — Implementation walkthrough (file map)

| Area | Where | Talking points |
|---|---|---|
| WS protocol | `api/schemas.py`, `api/ws.py` | typed frames, error frames don't kill the socket |
| Agent contract | `agent/base.py` | one protocol, one event stream |
| The loop | `agent/backends/custom.py` | ReAct in ~100 lines |
| Frameworks | `agent/backends/pydantic_ai.py`, `langgraph.py` | adapters, streaming APIs, checkpointer |
| Tools | `agent/tools/` | registry shared by all backends |
| MCP | `mcp/registry.py`, `mcp_servers/` | client + bundled servers |
| RAG | `rag/` | chunking → embeddings → hybrid store → rerank |
| Memory | `memory/` | Redis history + rolling summary |
| Evals | `evals/` | golden set, retrieval metrics, embedding comparison |
| Jobs | `worker.py` | taskiq broker + nightly cron |
| Frontend | `frontend/src/` | Pinia store owns the WS; tool cards |

Suggested flow: open the WS test (`tests/test_ws.py`) first — the protocol
in one screen — then walk `custom.py`, then show the same test suite
passing ×3 backends (`uv run pytest -q`).
