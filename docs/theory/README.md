# Theory — how this platform works, from zero

Working through this alongside the code? [project/learning-roadmap.md](../project/learning-roadmap.md) sequences these chapters with the source files and a command to run for each.

Self-contained explanations of every technology in this project, written for
someone who has **never** worked with LLMs, RAG, agents, or MCP. Each chapter
explains the concept in plain language, walks through how it works step by
step, shows exactly where it lives in this repository, and ends with the
questions you're likely to get at the workshop — with answers.

## Reading order

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

## The big picture — one request, end to end

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
