# 05 — Agent frameworks: Pydantic AI & LangGraph

## Why frameworks exist at all

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

## The one-protocol design (the thing to defend hardest)

```
agent/base.py        AgentBackend protocol + AgentEvent stream (the contract)
agent/tools/         ToolRegistry — ONE tool source for all backends
agent/backends/
  custom.py      98 lines   the loop, no dependencies
  pydantic_ai.py 266 lines  Pydantic AI runtime
  langgraph.py   278 lines  LangGraph runtime
```

Every backend receives the same tools, the same history, and must emit the
same event stream. Consequences:

- The **same WebSocket test suite runs ×3** — identical assertions pass on
  every runtime (the 203-test suite includes the WS suite parametrized ×3).
- The UI **switches runtimes per session** with a dropdown — same question,
  three frameworks, live.
- The choice of framework stays **reversible** — an architecture property,
  not a slide claim.

## Pydantic AI in five points

([`backends/pydantic_ai.py`](../../src/assistant/agent/backends/pydantic_ai.py))

1. From the team behind Pydantic; agents and tools are **typed** objects,
   validated like everything else in FastAPI-world.
2. **Model abstraction built in** — `openai:...`, `openai:...` strings or
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

## LangGraph in five points

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

## What the comparison actually showed

Full write-up with measured numbers:
[`docs/backend-comparison.md`](../reference/backend-comparison.md). The verdict
in one breath:

- **custom** — best for learning and debugging; smallest surface; you own
  everything.
- **pydantic-ai** — best effort-to-capability ratio for a typed FastAPI
  production service.
- **langgraph** — pays off when you need durable state, human-in-the-loop
  interrupts, or branching multi-agent workflows; steepest learning curve.

## Questions you might get

**"Why not LangChain?"** — Classic LangChain agents are the legacy API the
LangChain team itself superseded with LangGraph — which is what we
implemented. So LangChain's current answer *is* in the comparison.

**"Why not CrewAI / AutoGen?"** — Multi-agent orchestration frameworks; our
scope is a single assistant with tools. Adding a fourth backend later is
exactly one module implementing `AgentBackend`.

**"Isn't maintaining three backends wasteful?"** — The shared code (tools,
RAG, memory, WS) is written once; each backend is 100–280 lines. The price
of keeping the comparison alive is small, and the payoff is an evidence-based
framework decision — which was a stated goal of the bench project.

**"Which would you pick for production?"** — Pydantic AI for this shape of
service (typed, FastAPI-native, one-line tracing), and the platform makes
that a config default rather than a rewrite — that reversibility is the
actual answer.
