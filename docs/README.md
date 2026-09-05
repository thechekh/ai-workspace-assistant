# Documentation — the single source of truth

**The index of every document in this project: which folder answers which
kind of question, where to start for a given need, the topics to prepare
for a defence, and the reading order that covers all 45 pages.** Every
document lives under `docs/`, and every page follows
[the documentation standard](project/documentation-standard.md). Five
folders, five jobs:

| Folder | Answers | Start with |
|---|---|---|
| **[handbook/](handbook/README.md)** | *How do I run and operate **this** project?* | [handbook/README.md](handbook/README.md) |
| **[theory/](theory/README.md)** | *What **is** an LLM / embedding / agent / MCP?* (from zero) | [theory/README.md](theory/README.md) |
| **[reference/](reference/tools.md)** | *Precise details of one subject, measured* | [reference/tools.md](reference/tools.md) |
| **[project/](project/implementation-plan.md)** | *Why it's built this way, what's deferred, how to present it* | [project/implementation-plan.md](project/implementation-plan.md) |
| **[qanda/](qanda/README.md)** | *The hard questions, each followed by its grounded answer* | [qanda/README.md](qanda/README.md) |

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
[roadmap.md](roadmap.md) — twelve sessions,
~13 hours, covering all 38 source files: what to read, what to *run*, and a
self-check for each. Start here if you have more than an evening.

**I have to defend this project line by line** →
[reference/code-walkthrough.md](reference/code-walkthrough.md) — one question
followed through every layer, with the exact file and line at each step and
the question a reviewer asks there.

**I need to explain a quality number** → [reference/metrics.md](reference/metrics.md)
(and [reference/ragas.md](reference/ragas.md) for the judged one)
— recall@k, MRR and groundedness: what each measures, what it hides, and why
one is a CI gate and the other never can be.

**I'm asked "is this secure?"** → [reference/security.md](reference/security.md)
— the threat model, every control with a pointer to its code, and an
honest list of what is not built.

**I want to know what's next** → [project/future-tools.md](project/future-tools.md) —
every deferred tool and non-tool item, with its cost and revisit trigger; the
record of what's done is [project/implementation-plan.md](project/implementation-plan.md).

**I'm adding or rewriting a page** → [project/documentation-standard.md](project/documentation-standard.md)
— the fourteen rules every page follows, with ragas.md as the worked
example, and which of them the tests enforce.

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
| **Learning it from zero** | [roadmap.md](roadmap.md) — the sequenced study plan | [theory/README](theory/README.md) — the concepts | [handbook/02](handbook/02-getting-started.md) — run it first |
| **The whole system, in order** | [reference/code-walkthrough.md](reference/code-walkthrough.md) — boot → frame → memory → agent → LLM → tool → RAG → answer → audit | [handbook/01](handbook/01-project-overview.md) — the map | [theory/README](theory/README.md) — the concepts |
| **Quality metrics** | [reference/metrics.md](reference/metrics.md) — recall@k, MRR, groundedness in full | [handbook/05](handbook/05-rag-qdrant.md) — the measured table | [theory/09](theory/09-observability-and-evals.md) |
| **Security** | — | [reference/security.md](reference/security.md) — threat model + controls | [theory/12 §LLM & AI](theory/12-defense-qa.md) |
| **Any unfamiliar term** | [theory/11 — glossary](theory/11-glossary.md) | — | — |

### The reading roadmap

The step-by-step path through every document and every source file —
twelve sessions, each with what to read, what to run, and a self-check —
is [roadmap.md](roadmap.md). Its section 3 lists all 45 documents in reading
order, and a test fails the build if a page is ever left out of it.

**The short path.** No time for all of it? Rows **1 → 5 → 6 → 34 → 40** of that
table — what it is, the architecture, how to run it, the code walkthrough,
and the defence Q&A — is about two hours and covers most of what you will
be asked.

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
- [ragas.md](reference/ragas.md) — the LLM judge: what it is, how to run and read it, the negative control
- [logfire-langfuse.md](reference/logfire-langfuse.md) — Logfire vs Langfuse: purpose, comparison, how to enable and what each shows

### project/ — planning, decisions, delivery
- [tech-stack.md](project/tech-stack.md) — technology decisions and rationale
- [implementation-plan.md](project/implementation-plan.md) — phase-by-phase build history with acceptance evidence
- [description.md](project/description.md) — the project brief (English)
- [description-original.md](project/description-original.md) — the original brief, as received
- [workshop.md](project/workshop.md) — slides outline, live-demo script, walkthrough
- [documentation-standard.md](project/documentation-standard.md) — how pages are written: the rules, the exemplar, the tests that enforce them

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
