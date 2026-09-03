# Documentation — the single source of truth

Every document for this project lives under `docs/`. Four folders, four
different jobs:

| Folder | Answers | Start with |
|---|---|---|
| **[handbook/](handbook/README.md)** | *How do I run and operate **this** project?* | [handbook/README.md](handbook/README.md) |
| **[theory/](theory/README.md)** | *What **is** an LLM / embedding / agent / MCP?* (from zero) | [theory/README.md](theory/README.md) |
| **[reference/](reference/tools.md)** | *Precise details of one subject* | [reference/tools.md](reference/tools.md) |
| **[project/](project/implementation-plan.md)** | *Why it's built this way, what's deferred, how to present it* | [project/implementation-plan.md](project/implementation-plan.md) |

The repository [README](../README.md) stays at the root — it is the GitHub
landing page and the packaging `readme` — and links here.

## Where to go for what

**I'm new and want to run it** → [handbook/02-getting-started.md](handbook/02-getting-started.md)
(four run modes, every `.env` variable), then
[handbook/01-project-overview.md](handbook/01-project-overview.md) for the
architecture and a one-message walkthrough.

**I want to understand a concept** → [theory/](theory/README.md). Short on
time: chapters 01 → 03 → 04 → 06 → 12.

**I want to watch it work** → [handbook/07-observability.md](handbook/07-observability.md)
— every dashboard URL, log event, metric (with PromQL), and the
follow-one-message-through-five-layers drill.

**I'm testing it by hand** → [reference/testing.md](reference/testing.md) —
a tiered checklist (zero-infra → Docker → real model → observability).

**I need exact tool behaviour** → [reference/tools.md](reference/tools.md) —
every tool's purpose, parameters, return shape, errors, and implementation.

**I'm starting from zero and want to learn the whole thing** →
[project/learning-roadmap.md](project/learning-roadmap.md) — twelve sessions,
~13 hours, covering all 35 source files: what to read, what to *run*, and a
self-check for each. Start here if you have more than an evening.

**I have to defend this project line by line** →
[reference/code-walkthrough.md](reference/code-walkthrough.md) — one question
followed through every layer, with the exact file and line at each step and
the question a reviewer asks there.

**I need to explain a quality number** → [reference/metrics.md](reference/metrics.md)
— recall@k, MRR and groundedness: what each measures, what it hides, and why
one is a CI gate and the other never can be.

**I'm asked "is this secure?"** → [reference/security.md](reference/security.md)
— the threat model, every control with a pointer to its code, and an
honest list of what is not built.

**I want to know what's next** → [project/future-tools.md](project/future-tools.md) —
every deferred tool and non-tool item, with its cost and revisit trigger; the
record of what's done is [project/implementation-plan.md](project/implementation-plan.md).

## By topic — preparing to explain or defend the project

Each row is *concept first, then how this project does it, then the hard
questions*. Read across.

| Topic | Concept, from zero | How it works here | Depth / defence |
|---|---|---|---|
| **Purpose & scope** | — | [project/description.md](project/description.md) (the brief), [handbook/01](handbook/01-project-overview.md) | [theory/12 §Architecture](theory/12-defense-qa.md) |
| **Stack & technologies** | [theory/10](theory/10-infrastructure.md) | [handbook/03](handbook/03-technologies.md) — what, why, where | [project/tech-stack.md](project/tech-stack.md) — the decisions |
| **LLMs, tokens, cost** | [theory/01](theory/01-llm-basics.md) | [handbook/04](handbook/04-llm-models-tokens.md) — providers, retries, usage, pricing | [theory/12 §LLM & AI](theory/12-defense-qa.md) |
| **RAG** | [theory/03](theory/03-rag.md) | [handbook/05](handbook/05-rag-qdrant.md) — ingest + query pipeline | [theory/12 §RAG](theory/12-defense-qa.md) |
| **Embeddings & vector DB** | [theory/02](theory/02-embeddings-and-vector-search.md) | [handbook/05](handbook/05-rag-qdrant.md) — hybrid, RRF, rerank, gate | [theory/12 §Vector database](theory/12-defense-qa.md) |
| **Tools / function calling** | [theory/04](theory/04-tool-calling-and-agents.md) | [reference/tools.md](reference/tools.md) — every tool in full | [handbook/06](handbook/06-tools-mcp.md) |
| **MCP** | [theory/06](theory/06-mcp.md) | [handbook/06](handbook/06-tools-mcp.md) | [theory/12 §Agents, tools & MCP](theory/12-defense-qa.md) |
| **Agents & frameworks** | [theory/04](theory/04-tool-calling-and-agents.md), [theory/05](theory/05-agent-frameworks.md) | [handbook/08](handbook/08-agents-memory-ws.md) | [reference/backend-comparison.md](reference/backend-comparison.md) — measured |
| **Memory** | [theory/07](theory/07-memory.md) | [handbook/08](handbook/08-agents-memory-ws.md) | — |
| **Real-time / WebSocket** | [theory/08](theory/08-realtime-websockets.md) | [handbook/08](handbook/08-agents-memory-ws.md) — the frame protocol | — |
| **Observability & cost** | [theory/09](theory/09-observability-and-evals.md) | [handbook/07](handbook/07-observability.md) — every surface + PromQL | [theory/12 §Observability](theory/12-defense-qa.md) |
| **Testing & evals** | [theory/09](theory/09-observability-and-evals.md) | [handbook/09](handbook/09-testing-operations.md) | [reference/testing.md](reference/testing.md) — manual checklist |
| **Learning it from zero** | [project/learning-roadmap.md](project/learning-roadmap.md) — the sequenced study plan | [theory/README](theory/README.md) — the concepts | [handbook/02](handbook/02-getting-started.md) — run it first |
| **The whole system, in order** | [reference/code-walkthrough.md](reference/code-walkthrough.md) — boot → frame → memory → agent → LLM → tool → RAG → answer → audit | [handbook/01](handbook/01-project-overview.md) — the map | [theory/README](theory/README.md) — the concepts |
| **Quality metrics** | [reference/metrics.md](reference/metrics.md) — recall@k, MRR, groundedness in full | [handbook/05](handbook/05-rag-qdrant.md) — the measured table | [theory/09](theory/09-observability-and-evals.md) |
| **Security** | — | [reference/security.md](reference/security.md) — threat model + controls | [theory/12 §LLM & AI](theory/12-defense-qa.md) |
| **Any unfamiliar term** | [theory/11 — glossary](theory/11-glossary.md) | — | — |

### The reading roadmap — all 42 documents, in order

Read top to bottom and you will have read **every** document in the
repository, each one landing when you have the background for it. Tick them
off; the whole path is roughly **8–10 hours** of reading.

This is the *reading* order. If you want to read the **code** alongside it,
[project/learning-roadmap.md](project/learning-roadmap.md) pairs these
chapters with source files and commands to run.

#### Stage 1 · Orient — what is this? *(~30 min)*

| # | Document | Why here |
|---|---|---|
| 1 | [../README.md](../README.md) | The landing page: what it is, quickstart, headline numbers |
| 2 | [README.md](README.md) *(this page)* | How the documentation is organised |
| 3 | [project/description.md](project/description.md) | The brief — what was actually asked for |
| 4 | [handbook/01-project-overview.md](handbook/01-project-overview.md) | Architecture diagram + one message end to end |

#### Stage 2 · Run it *(~45 min — do it, don't just read)*

| # | Document | Why here |
|---|---|---|
| 5 | [handbook/README.md](handbook/README.md) | The index, and every localhost URL in one place |
| 6 | [handbook/02-getting-started.md](handbook/02-getting-started.md) | Both modes (mocked / OpenAI) and every `.env` variable |
| 7 | [handbook/03-technologies.md](handbook/03-technologies.md) | Every technology: what it does, why chosen, where it lives |

#### Stage 3 · The concepts, from zero *(~3 h)*

Nothing here is specific to this project — it is the vocabulary. Skip any
chapter you already know cold.

| # | Document | Covers |
|---|---|---|
| 8 | [theory/README.md](theory/README.md) | The course map |
| 9 | [theory/01-llm-basics.md](theory/01-llm-basics.md) | Tokens, context, streaming, hallucination |
| 10 | [theory/02-embeddings-and-vector-search.md](theory/02-embeddings-and-vector-search.md) | Vectors, similarity, dense vs sparse, HNSW |
| 11 | [theory/03-rag.md](theory/03-rag.md) | Chunking, retrieval, grounding, recall@k, MRR |
| 12 | [theory/04-tool-calling-and-agents.md](theory/04-tool-calling-and-agents.md) | Function calling, the ReAct loop |
| 13 | [theory/05-agent-frameworks.md](theory/05-agent-frameworks.md) | Pydantic AI, LangGraph, and why write a loop yourself |
| 14 | [theory/06-mcp.md](theory/06-mcp.md) | The protocol, transports, namespacing |
| 15 | [theory/07-memory.md](theory/07-memory.md) | Short vs long term, rolling summaries |
| 16 | [theory/08-realtime-websockets.md](theory/08-realtime-websockets.md) | Streaming, WebSocket vs SSE |
| 17 | [theory/09-observability-and-evals.md](theory/09-observability-and-evals.md) | Traces, metrics, and measuring quality |
| 18 | [theory/10-infrastructure.md](theory/10-infrastructure.md) | Redis, Qdrant, Docker, task queues |

#### Stage 4 · How **this** project does each of them *(~3 h)*

Same order as stage 3, so each chapter answers "…and here is how we built it".

| # | Document | Pairs with |
|---|---|---|
| 19 | [handbook/04-llm-models-tokens.md](handbook/04-llm-models-tokens.md) | theory/01 — providers, retries, usage, cost, limits |
| 20 | [handbook/05-rag-qdrant.md](handbook/05-rag-qdrant.md) | theory/02–03 — the full pipeline + measured quality |
| 21 | [handbook/06-tools-mcp.md](handbook/06-tools-mcp.md) | theory/04, 06 — the tool inventory and the execution seam |
| 22 | [handbook/08-agents-memory-ws.md](handbook/08-agents-memory-ws.md) | theory/05, 07, 08 — 3 backends, the frame protocol, summarization |
| 23 | [handbook/07-observability.md](handbook/07-observability.md) | theory/09 — logs, metrics, traces, health, audit |
| 24 | [handbook/09-testing-operations.md](handbook/09-testing-operations.md) | theory/09 — the suite map, ops, troubleshooting |

#### Stage 5 · Into the code *(~2 h)*

| # | Document | Why here |
|---|---|---|
| 25 | [reference/code-walkthrough.md](reference/code-walkthrough.md) | One question through every layer, file and line by line |
| 26 | [project/learning-roadmap.md](project/learning-roadmap.md) | The twelve-session plan for reading all 35 source files |

#### Stage 6 · Precise references — read when you need them

Not linear; each answers one narrow question completely.

| # | Document | Answers |
|---|---|---|
| 27 | [reference/tools.md](reference/tools.md) | Every tool: parameters, returns, errors, implementation |
| 28 | [reference/metrics.md](reference/metrics.md) | recall@k, MRR, groundedness — and what each one hides |
| 29 | [reference/backend-comparison.md](reference/backend-comparison.md) | custom vs Pydantic AI vs LangGraph, measured |
| 30 | [reference/security.md](reference/security.md) | Threat model, every control, and what is deliberately absent |
| 31 | [reference/testing.md](reference/testing.md) | The manual checklist, tiered from zero-infra to real model |

#### Stage 7 · Why it looks like this *(~1 h)*

The decisions and the history — this is where "why not X?" gets answered.

| # | Document | Why here |
|---|---|---|
| 32 | [project/tech-stack.md](project/tech-stack.md) | Every technology decision and the alternatives rejected |
| 33 | [project/implementation-plan.md](project/implementation-plan.md) | Phase-by-phase build history with acceptance evidence |
| 34 | [project/description-original.md](project/description-original.md) | The original brief as received — compare with what shipped |

#### Stage 8 · Rehearse the defence *(~1.5 h)*

| # | Document | Why last |
|---|---|---|
| 35 | [theory/11-glossary.md](theory/11-glossary.md) | 119 terms — cover the definitions and say them aloud |
| 36 | [theory/12-defense-qa.md](theory/12-defense-qa.md) | The hard questions, with answers. Do this without notes |
| 37 | [project/workshop.md](project/workshop.md) | Slide outline and the click-by-click demo script |
| 38 | [project/demo-runbook.md](project/demo-runbook.md) | Running the demo on the real stack: keys, infra, costs |
| 39 | [reference/localhost.md](reference/localhost.md) | Every localhost link once it runs: dashboards, logs, and browsing the vector DB |
| 40 | [project/future-tools.md](project/future-tools.md) | Every tool considered and deferred: cost, verdict, trigger to revisit |
| 41 | [qanda/README.md](qanda/README.md) | The 69 hard questions — 48 general, 21 on this codebase — each followed by its grounded answer with measured numbers |

#### Stage 9 · Repository conventions — only if you will contribute

| # | Document | Answers |
|---|---|---|
| 42 | [../CLAUDE.md](../CLAUDE.md) | Instructions for AI coding agents working in this repo |

**The short path.** No time for all of it? **1 → 4 → 6 → 25 → 38** — what it
is, the architecture, how to run it, the code walkthrough, and the defence
Q&A. That is about two hours and covers most of what you will be asked.

**Presenting it?** [project/workshop.md](project/workshop.md) has the slide
outline, a click-by-click demo script, and the file-map walkthrough.

## Full index

### handbook/ — operating this project
| # | Chapter |
|---|---|
| — | [Index + every localhost URL](handbook/README.md) |
| 01 | [Project overview](handbook/01-project-overview.md) — architecture, one message end-to-end, repo layout |
| 02 | [Getting started](handbook/02-getting-started.md) — 4 run modes, full `.env` reference |
| 03 | [Technologies](handbook/03-technologies.md) — every technology: what, why, where |
| 04 | [LLM, models, tokens & cost](handbook/04-llm-models-tokens.md) — providers, retries, usage, pricing, rate limits |
| 05 | [RAG & Qdrant](handbook/05-rag-qdrant.md) — ingest + query pipeline, relevance gate, measured quality |
| 06 | [Tools & MCP](handbook/06-tools-mcp.md) — the inventory and how a call executes |
| 07 | [Observability](handbook/07-observability.md) — logs, metrics, traces, health, audit |
| 08 | [Agents, memory & WebSocket](handbook/08-agents-memory-ws.md) — 3 backends, WS frames, summarization |
| 09 | [Testing & operations](handbook/09-testing-operations.md) — suite map, ops commands, troubleshooting |

### theory/ — concepts from zero
| # | Chapter |
|---|---|
| — | [Reading order + the big picture](theory/README.md) |
| 01 | [LLM basics](theory/01-llm-basics.md) |
| 02 | [Embeddings & vector search](theory/02-embeddings-and-vector-search.md) |
| 03 | [RAG](theory/03-rag.md) |
| 04 | [Tool calling & agents](theory/04-tool-calling-and-agents.md) |
| 05 | [Agent frameworks](theory/05-agent-frameworks.md) |
| 06 | [MCP](theory/06-mcp.md) |
| 07 | [Conversation memory](theory/07-memory.md) |
| 08 | [Real-time & WebSockets](theory/08-realtime-websockets.md) |
| 09 | [Observability & evals](theory/09-observability-and-evals.md) |
| 10 | [Infrastructure](theory/10-infrastructure.md) |
| 11 | [Glossary](theory/11-glossary.md) |
| 12 | [Defense Q&A](theory/12-defense-qa.md) |

### reference/ — single-topic references
- [tools.md](reference/tools.md) — the complete tool reference (native + MCP)
- [testing.md](reference/testing.md) — manual testing checklist, tiered
- [backend-comparison.md](reference/backend-comparison.md) — custom vs Pydantic AI vs LangGraph, measured
- [security.md](reference/security.md) — threat model, what is enforced, what is deliberately not

### project/ — planning, decisions, delivery
- [tech-stack.md](project/tech-stack.md) — technology decisions and rationale
- [implementation-plan.md](project/implementation-plan.md) — phase-by-phase build history with acceptance evidence
- [description.md](project/description.md) — the project brief (English)
- [description-original.md](project/description-original.md) — the original brief, as received
- [workshop.md](project/workshop.md) — slides outline, live-demo script, walkthrough

## Not documentation (deliberately outside `docs/`)

Two sets of Markdown files live elsewhere on purpose:

- **[`evals/corpus/`](../evals/corpus/)** — the retrieval **test fixture**:
  the documents the golden set's expected answers live in. Referenced by
  [evals/run_retrieval.py](../evals/run_retrieval.py) and
  [evals/golden.yaml](../evals/golden.yaml). The running app does not load it —
  the knowledge base starts empty and is filled at runtime.
- **[`evals/results-embeddings.md`](../evals/results-embeddings.md)** —
  generated output, written by `evals/compare_embeddings.py` next to the
  script that produces it.
