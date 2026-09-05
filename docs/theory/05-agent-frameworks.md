# 05 — Agent frameworks: Pydantic AI & LangGraph

**What this chapter answers: what a framework buys over the raw loop in the
previous chapter, and what Pydantic AI and LangGraph each cost, measured on
identical ground.** It does not re-derive the loop itself — see
[04-tool-calling-and-agents.md](04-tool-calling-and-agents.md) for that; this
chapter is the comparison built on top of it.

## 1. Why frameworks exist at all

Chapter 04 showed the whole agent loop in ~100 lines. Frameworks exist
because production agents accumulate the same needs over and over: provider
abstraction, typed/validated tool arguments, streaming plumbing, retries,
structured outputs, state persistence, multi-step workflows, observability
hooks. A framework is that boilerplate, packaged — at the price of learning
its abstractions and debugging through its layers.

This project's deliberately unusual move: we implemented the same agent
**three times** — no framework, Pydantic AI, LangGraph — behind one
interface, so the trade-offs are *measured on identical ground* instead of
argued from blog posts.

## 2. The one-protocol design (the thing to defend hardest)

```
agent/base.py        AgentBackend protocol + AgentEvent stream (the contract)
agent/tools/         ToolRegistry — ONE tool source for all backends
agent/backends/
  custom.py      98 lines   the loop, no dependencies
  pydantic_ai.py 286 lines  Pydantic AI runtime
  langgraph.py   278 lines  LangGraph runtime
```

Line counts measured with `wc -l src/assistant/agent/backends/*.py`,
2026-09-04 — see [backend-comparison.md](../reference/backend-comparison.md)
for the full table these numbers are drawn from.

Every backend receives the same tools, the same history, and must emit the
same event stream. Consequences:

- The **same WebSocket test suite runs ×3** — identical assertions pass on
  every runtime (the 573-test suite includes the WS suite parametrized ×3).
- The UI **switches runtimes per session** with a dropdown — same question,
  three frameworks, live.
- The choice of framework stays **reversible** — an architecture property,
  not a slide claim.

The alternative to "one protocol, three modules in `main`" was three git
branches, one per phase — and [tech-stack.md](../project/tech-stack.md) named
that option and rejected it before writing a single backend: the shared code
(WS server, RAG, MCP registry, memory) would drift across branches, two
backends could never be demoed side by side in the same running app, and
every improvement would need merging three times over. Short-lived feature
branches are still used *while building* each phase — the comparison itself
just never gets to live on one.

## 3. Pydantic AI in five points

([`backends/pydantic_ai.py`](../../src/assistant/agent/backends/pydantic_ai.py))

1. From the team behind Pydantic; agents and tools are **typed** objects,
   validated like everything else in FastAPI-world.
2. **Model abstraction built in** — `openai:...`-style provider strings or
   model objects; it replaced our LLM client entirely for this backend.
3. Our raw-JSON-schema tools mapped 1:1 via `Tool.from_schema` — the shared
   registry needed one small adapter function.
4. Streaming via the **graph iteration API** (`agent.iter`) — typed events
   for text deltas, tool calls, tool results; worked first try.
5. Native extras we exploit or note: `logfire.instrument_pydantic_ai()`
   (one-line tracing, chapter 09) and first-class MCP client support (we
   route MCP through our shared registry instead — for comparability).

Cost of admission we're honest about: the offline fake had to be
**re-implemented** against its `FunctionModel` interface (~45 lines) because
this backend doesn't speak our `LLMClient` protocol.

## 4. LangGraph in five points

([`backends/langgraph.py`](../../src/assistant/agent/backends/langgraph.py))

1. From the LangChain ecosystem; you model the agent as an **explicit state
   graph** — ours: `START → agent ⇄ tools → END`, nodes are async functions
   over a shared `messages` state.
2. **Checkpointing** is the flagship: the graph persists its state per
   `thread_id` (we compile with an in-memory saver per turn; Redis/Postgres
   savers make runs durable and resumable — the real production draw).
3. Built-in **recursion limit** replaces our hand-written iteration bound
   (`GraphRecursionError` → same "hit the limit" answer as the other two).
4. It's coupled to LangChain's model/message types, so we wrote a ~95-line
   `BaseChatModel` **adapter over our own `LLMClient` protocol** — with a
   payoff: FakeLLM, OpenAI, and even the scripted test LLMs run on LangGraph
   unchanged (the recursion test literally imports the custom loop's
   `ScriptedLLM`).
5. Streaming multiplexes token chunks and node updates
   (`stream_mode=["messages", "updates"]`) — powerful, and the fiddliest
   API of the three (two real gotchas cost debugging time).

## 5. What the comparison actually showed

Full write-up with measured numbers:
[`docs/backend-comparison.md`](../reference/backend-comparison.md). The
numbers side by side:

| Backend | File | LoC | Framework-adapter cost | Best for |
|---|---|---:|---|---|
| custom | [`custom.py`](../../src/assistant/agent/backends/custom.py) | **98** | none — talks to `LLMClient` directly | learning and debugging; smallest surface, full control |
| Pydantic AI | [`pydantic_ai.py`](../../src/assistant/agent/backends/pydantic_ai.py) | **286** | ~45 lines (`FunctionModel` fake) + ~25 (model builder) | best effort-to-capability ratio for a typed FastAPI service |
| LangGraph | [`langgraph.py`](../../src/assistant/agent/backends/langgraph.py) | **278** | ~95 lines (`BaseChatModel` adapter) + ~35 (message conversion) | durable state, human-in-the-loop interrupts, branching multi-agent workflows |

The verdict in one breath — the reasoning a table can't carry:

- **custom** — best for learning and debugging; smallest surface; you own
  everything.
- **pydantic-ai** — best effort-to-capability ratio for a typed FastAPI
  production service.
- **langgraph** — pays off when you need durable state, human-in-the-loop
  interrupts, or branching multi-agent workflows; steepest learning curve.

## 6. Questions you might get

**"Why not LangChain?"** — Classic LangChain agents are the legacy API the
LangChain team itself superseded with LangGraph — which is what we
implemented. So LangChain's current answer *is* in the comparison.

**"Why not CrewAI / AutoGen?"** — Multi-agent orchestration frameworks; our
scope is a single assistant with tools. Adding a fourth backend later is
exactly one module implementing `AgentBackend`.

**"Isn't maintaining three backends wasteful?"** — The shared code (tools,
RAG, memory, WS) is written once; each backend is 98–286 lines. The price
of keeping the comparison alive is small, and the payoff is an evidence-based
framework decision — which was a stated goal of the bench project.

**"Which would you pick for production?"** — Pydantic AI for this shape of
service (typed, FastAPI-native, one-line tracing), and the platform makes
that a config default rather than a rewrite — that reversibility is the
actual answer.

## 7. Reading it honestly

- **The comparison runs on one small POC-shaped workload.** Few tools, short
  turns, one small model. "Best effort-to-capability ratio" is measured
  here, not proven to generalize to a large multi-team codebase with dozens
  of tools.
- **LoC alone flatters a framework that replaces the model layer.** The
  custom loop's 98 lines lean on `llm/client.py`'s hardening (429 backoff,
  `tool_use_failed` retry, leaked-`<function>` salvage) for free. Pydantic AI
  routes around that client entirely, so it had to re-earn parts of the same
  hardening inside its own 286 — the fair comparison is line count *plus*
  what silently stopped applying, not line count alone
  ([backend-comparison.md](../reference/backend-comparison.md) has the
  incident that exposed this).
- **LangGraph's flagship feature is not exercised here.** The graph compiles
  with an in-memory checkpointer, fresh per turn — durable Redis/Postgres
  persistence is deliberately deferred
  ([future-tools.md](../project/future-tools.md)), so this comparison shows
  LangGraph's mechanics, not the production persistence story it's chosen
  for.
- **"Debuggability" and "effort" were judged against one small model and the
  offline fake.** A harder model, or a much larger tool set, could change
  which framework's guardrails matter most.
- **The three backends were compared once, not soaked.** No load test or
  long-run comparison exists between them — only the same 18-question-scale
  functional parity the WS suite checks ×3.

## 8. Related

- [04-tool-calling-and-agents.md](04-tool-calling-and-agents.md) — the loop mechanics all three backends in this chapter implement
- [../reference/backend-comparison.md](../reference/backend-comparison.md) — the full measured comparison this chapter summarizes
- [06-mcp.md](06-mcp.md) — the tool-server protocol all three backends share unchanged
- [../project/tech-stack.md](../project/tech-stack.md) — the phased decision, and the branches-vs-modules reasoning in full
- [../project/future-tools.md](../project/future-tools.md) — the LangGraph Redis checkpointer, deferred, and what would trigger building it
