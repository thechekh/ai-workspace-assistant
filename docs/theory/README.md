# Theory — how this platform works, from zero

**What this index answers: which chapter explains which concept, in what
order, and how one request moves through all of them end to end.** It does
not explain any concept itself — that's each numbered chapter below — and
it is not the project-specific "how we built it" side of the same topics,
which is [docs/README.md](../README.md)'s by-topic table.

Working through this alongside the code? [project/learning-roadmap.md](../project/learning-roadmap.md) sequences these chapters with the source files and a command to run for each.

Self-contained explanations of every technology in this project, written for
someone who has **never** worked with LLMs, RAG, agents, or MCP. Each chapter
explains the concept in plain language, walks through how it works step by
step, shows exactly where it lives in this repository, and ends with the
questions you're likely to get at the workshop — with answers.

## 1. Reading order

| # | Chapter | You'll understand |
|---|---|---|
| 01 | [LLM basics](01-llm-basics.md) | What a language model is, tokens, context windows, the chat API, why it's stateless |
| 02 | [Embeddings & vector search](02-embeddings-and-vector-search.md) | How text becomes numbers, similarity, Qdrant, dense vs sparse, hybrid search, reranking |
| 03 | [RAG](03-rag.md) | How the assistant answers from *our* docs: chunking → embedding → retrieval → grounded answers, and how we measure it |
| 04 | [Tool calling & agents](04-tool-calling-and-agents.md) | How a model "does things": the tool-call contract and the ReAct loop, line by line |
| 05 | [Agent frameworks](05-agent-frameworks.md) | What Pydantic AI and LangGraph add over a hand-written loop, and our one-protocol design |
| 06 | [MCP](06-mcp.md) | The Model Context Protocol: why it exists, how servers/clients talk, our registry and servers |
| 07 | [Conversation memory](07-memory.md) | Why chat needs memory, and how rolling summarization keeps prompts bounded |
| 08 | [Real-time & WebSockets](08-realtime-websockets.md) | Streaming tokens to the browser: our typed WS protocol |
| 09 | [Observability & evals](09-observability-and-evals.md) | Tracing LLM apps, and how we test something nondeterministic deterministically |
| 10 | [Infrastructure](10-infrastructure.md) | The supporting cast: FastAPI, Redis, Qdrant, Docker, uv/ruff/pyright, auth |
| 11 | [Glossary](11-glossary.md) | Every term in one line |
| 12 | [Defense Q&A](12-defense-qa.md) | The hard questions and strong answers |

**Short on time?** Read 01 → 03 → 04 → 06 → 12. That covers the demo's
storyline and the most likely questions.

## 2. The big picture — one request, end to end

```
User types: "What is our deployment architecture?"
│
▼  (08) WebSocket frame {type: user_message}
FastAPI /chat endpoint
│
▼  (07) ConversationMemory: rolling summary + recent turns from Redis
Agent backend (04, 05) — custom | pydantic-ai | langgraph, same contract
│
▼  (04) LLM step 1: model sees the question + tool definitions
LLM decides: call search_docs(query="deployment architecture")
│
▼  (03) RAG: embed query (02) → Qdrant hybrid search (02) → rerank → top chunks
tool result: "[architecture/deployment.md — CI/CD] ... ArgoCD ..."
│
▼  (04) LLM step 2: model sees the chunks, writes a grounded answer
tokens stream back over the WS (08), UI renders tool cards + markdown
│
▼  (07) final answer appended to Redis history
Done — and every step is traceable (09) and tested (09)
```

MCP tools (06) enter the same flow: `github__list_pull_requests` and
`code__search_code` sit in the same tool registry as `search_docs`, so the
loop in (04) treats them identically.

Every step in that path is traceable and tested — 382 tests, offline, $0
(2026-09-04, `uv run pytest -q`) — and the retrieval step is additionally
*measured*: recall@5 = 1.00 on the golden set with the default
hybrid+rerank configuration (2026-09-04, `uv run python
evals/run_retrieval.py --memory`). "We built it" and "we checked it" are
answered by different chapters (09), but neither is left to memory.

## 3. Related

- [project/learning-roadmap.md](../project/learning-roadmap.md) — these chapters paired with source files and a command to run for each
- [docs/README.md](../README.md) — the full documentation index, including the by-topic table this course's project-specific counterpart lives in
- [11 — Glossary](11-glossary.md) — every term from every chapter, one line each
- [12 — Defense Q&A](12-defense-qa.md) — the hard questions, answered, once the chapters are read
- [handbook/README.md](../handbook/README.md) — how to run the project this course explains
