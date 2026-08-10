# Agent Backend Comparison — custom loop vs Pydantic AI vs LangGraph

Three runtimes implement the **same `AgentBackend` protocol**, receive the
**same `ToolRegistry`** (native `search_docs` + MCP-adapted tools), emit the
**same `AgentEvent` stream**, and pass the **same WebSocket test suite**
(every WS test is parametrized ×3). Switch per session with the UI dropdown
(`?backend=`). That controlled setup is what makes this comparison honest:
everything below differs *only* because of the framework.

Measured on this repository (line counts via `wc -l`, docstrings included).

## The numbers

| | custom loop | Pydantic AI | LangGraph |
|---|---:|---:|---:|
| Backend file LoC | **98** | **266** | **278** |
| Inherits the shared provider hardening | yes | **no — re-implemented** | yes |
| …of which framework-adapter code | 0 | ~45 (FunctionModel fake) + ~25 (model builder) | ~95 (`BaseChatModel` adapter) + ~35 (message conversion) |
| Extra runtime deps | none (shares `llm/client.py`) | `pydantic-ai` (+ its provider SDKs) | `langgraph` + `langchain-core` |
| Offline fake for tests/demo | free — FakeLLM speaks our protocol | needs a `FunctionModel` twin; both now share `llm/fake.py` after the hand-copied version silently drifted | free — the adapter lets FakeLLM/ScriptedLLM run unchanged |
| Loop bound | hand-written `for` loop | framework internal (usage limits available) | built-in `recursion_limit` → `GraphRecursionError` |

Context for fairness: the custom loop leans on our shared `llm/client.py`
(445 lines, including the provider-hardening layer) — but it also serves LangGraph (via the adapter), the
dev fake, and the test scripting, so it isn't a custom-loop-only cost.

## Dimension by dimension

### Streaming

- **custom** — we own every byte: `stream_step()` yields text deltas and
  accumulated tool calls; mapping to WS events is trivial. Nothing to learn,
  nothing hidden.
- **pydantic-ai** — `agent.iter()` exposes graph nodes; request-node streams
  yield `PartStartEvent`/`PartDeltaEvent`, tool nodes yield
  `FunctionToolCallEvent`/`FunctionToolResultEvent`. Typed, predictable,
  worked on the first run.
- **langgraph** — `graph.astream(stream_mode=["messages", "updates"])`
  multiplexes token chunks and node outputs. Two gotchas cost real time: the
  node must call the model's *public* `astream()` (otherwise token callbacks
  never fire and `stream_mode="messages"` stays silent), and the combined
  stream's payload type is a union that needs runtime guards.

### Tool wiring & MCP effort

Identical by design: all three consume the shared registry, so MCP tools
(`code__search_code`, `github__list_pull_requests`) cost **zero extra
effort** per backend. The interesting difference is what each framework
*wanted*:

- custom: tools are just name + JSON schema + async handler — our native shape.
- pydantic-ai: `Tool.from_schema(fn, name, description, json_schema)` mapped
  our shape 1:1 (one small wrapper). It also offers first-class MCP client
  support if you don't have your own registry.
- langgraph: expects LangChain `BaseTool`s or OpenAI-format dicts bound onto
  the model; we bound the dicts and hand-wrote a 12-line tools node that
  calls the registry — easier than adapting to `BaseTool`.

### The model boundary (biggest architectural difference)

- custom talks to our `LLMClient` protocol directly.
- pydantic-ai replaces the model layer entirely with its own abstraction —
  clean, but it means provider config exists twice (ours + theirs), and the
  offline fake had to be **re-implemented** against `FunctionModel`.

  This is the dimension that cost the most, and it did not show up until a
  live run. Replacing the model layer also replaces everything wrapped around
  it: the 429 backoff, the `tool_use_failed` retry, the `failed_generation`
  salvage and the leaked-`<function>` parsing all live in
  `llm/client.py`, which this backend never reaches. Offline the three
  backends were provably identical (`test_fake_parity.py`); against real Groq,
  the same knowledge-base question that custom and langgraph answered was a
  hard error here, reproducibly, because llama had emitted one malformed tool
  call and nothing retried it. The retries are now re-implemented in the
  backend — sharing the *policy* (`rate_limit_delay`, `is_tool_use_failure`)
  even though it cannot share the loop. That is the real price of swapping a
  framework's model layer for your own: not the adapter, the invariants that
  quietly stop applying.
- langgraph is coupled to LangChain's `BaseChatModel`/message types, so we
  wrote a 95-line adapter over our protocol. Upside discovered: once the
  adapter existed, **every** fake and scripted LLM we already had worked on
  LangGraph for free — the recursion/tool tests literally import the same
  `ScriptedLLM` used against the custom loop.

### Memory & state

Cross-turn memory is shared Redis for all three (deliberate — backends stay
interchangeable mid-session). Native stories differ:

- custom: none — you build the messages array yourself (which is also why
  it's the best teaching artifact).
- pydantic-ai: `message_history` parameter; one subtlety — the system prompt
  is *not* re-applied when history is passed, so we fold it into the first
  request ourselves.
- langgraph: the standout — compiled with an `InMemorySaver` checkpointer
  (fresh `thread_id` per turn here); swapping to Redis/Postgres savers gives
  durable, resumable, time-travelable graph state. If we didn't already have
  Redis history, LangGraph's persistence could replace it outright.

### Observability

- pydantic-ai: `logfire.instrument_pydantic_ai()` — one line, spans for every
  agent run and tool call (wired in `observability.py`).
- langgraph: native ecosystem answer is LangSmith; generic OTel goes through
  LangChain callbacks — workable, more assembly required.
- custom: you instrument what you log — total freedom, zero freebies.

### Debuggability

- custom: a stack trace points at ~100 lines you wrote. Fastest to debug.
- pydantic-ai: typed events and clear exceptions; one indirection layer.
- langgraph: graph engine + callback plumbing + LangChain unions between you
  and the failure. Powerful once internalized, most expensive to learn.

## Verdict

| Use case | Pick |
|---|---|
| Learning how agents actually work; minimal surface; full control | **custom loop** |
| Production FastAPI service wanting types, provider abstraction, MCP, Logfire out of the box | **Pydantic AI** |
| Complex stateful workflows: durable checkpoints, human-in-the-loop interrupts, multi-agent graphs | **LangGraph** |

For *this* platform's demo scope, the honest ranking is custom (clarity) →
pydantic-ai (best effort-to-capability ratio) → langgraph (pays off only
once you need its persistence/branching machinery). The most valuable
artifact, though, is the setup itself: one protocol, one tool registry, one
test suite — so the choice stays reversible with a dropdown.
