# 08 — Agents, memory & the WebSocket protocol

## The agent contract (one interface, three runtimes)

Every backend implements the same tiny protocol
([agent/base.py](../../src/assistant/agent/base.py)):

```python
class AgentBackend(Protocol):
    def run(self, history: list[ChatMessage], user_message: str) -> AsyncIterator[AgentEvent]
```

…where `AgentEvent` is `TokenEvent | ToolCallEvent | ToolResultEvent |
FinalEvent | ErrorEvent`. The WS layer and frontend consume events and never
know which runtime produced them — that's what makes the per-session
switcher possible (`?backend=` / the UI dropdown; unknown names fall back to
the default; history carries over because it lives in Redis, not the agent).

| Backend | File | What it is | Notable |
|---|---|---|---|
| `custom` *(default)* | [backends/custom.py](../../src/assistant/agent/backends/custom.py) | Hand-written ReAct loop over the LLM client | The reference: smallest, fully instrumented via `InstrumentedLLM` |
| `pydantic_ai` | [backends/pydantic_ai.py](../../src/assistant/agent/backends/pydantic_ai.py) | Pydantic AI `Agent` | Runs its **own** model layer (token stats fall back to estimates); tools adapted via `Tool.from_schema` → still hit `Tool.run` |
| `langgraph` | [backends/langgraph.py](../../src/assistant/agent/backends/langgraph.py) | LangGraph state graph | Wraps our LLM client as a LangChain chat model; checkpointing in-memory (Redis saver is backlog) |

Measured comparison (code size, latency, behavior parity):
[docs/backend-comparison.md](../reference/backend-comparison.md). The custom
loop's rule: **max 6 tool-loop iterations**, then it answers with what it
has; every backend gets the identical tool registry and system prompt.

## The WebSocket protocol (`/chat`)

Typed frames, defined in [api/schemas.py](../../src/assistant/api/schemas.py)
and mirrored in [frontend/src/types.ts](../../frontend/src/types.ts).

**Client → server:** `{"type": "user_message", "content": "..."}`

**Server → client, in order per turn:**

| frame | fields | when |
|---|---|---|
| `session` | `session_id` | once, on connect (client stores it; reconnects pass `?session_id=` to resume) |
| `token` | `content` | each streamed answer piece |
| `tool_call` | `tool`, `arguments` | agent invokes a tool (UI shows a card) |
| `tool_result` | `tool`, `result` | tool finished (fills the card) |
| `final` | `content` | the complete answer (authoritative text) |
| `turn` | `turn_id, backend, duration_ms, first_token_ms, llm_steps, tool_calls[], prompt_tokens, completion_tokens, usage_estimated, cost_usd` | **after** `final` — the stats line |
| `error` | `message` | anything failed; the socket stays open, mapped to friendly text (chapter 04) |

Connection query params: `?session_id=` (resume), `?backend=` (runtime),
`?token=` (auth — browsers can't set WS headers). Invalid JSON in → an
`error` frame, socket survives.

## Conversation memory (why prompts don't grow forever)

Full transcripts live in Redis (`SessionStore`, 24 h TTL) — but the model
never sees all of it. [`ConversationMemory`](../../src/assistant/memory/conversation.py)
builds each turn's context as:

```
system prompt + [rolling summary] + last N verbatim messages + new question
```

When the un-summarized tail exceeds `HISTORY_CHAR_BUDGET` (8000 chars ≈ 2k
tokens), everything but the last `HISTORY_KEEP_RECENT` (6) messages is
**folded into the summary** by the summarizer (the same LLM; a deterministic
fold with FakeLLM). Result, proven by test: after enough turns the prompt
size *stops growing* — identically on all three backends. The summary is
stored per session in Redis, so it survives reconnects and backend switches.

## Sessions & the audit trail in Redis

Per `session_id` (TTL 24 h):

| Redis key | Contents |
|---|---|
| `session:{id}` | the transcript (list of ChatMessage JSON) |
| `session:{id}:summary` | the rolling summary |
| `session:{id}:turns` | audit records (last 50; chapter 07) |

`fakeredis://` gives the identical API in-process — sessions just die with
the server.

## Where the turn logic lives

[`api/ws.py`](../../src/assistant/api/ws.py) `_handle_turn` is the conductor:
builds context, streams agent events to the socket, tracks first-token
latency and the audit timeline, closes the `agent.turn` span, computes
stats/cost, sends the `turn` frame, writes `turn.summary`, stores the audit
record — and converts any exception into a friendly `error` frame without
killing the socket. Read that one function and you understand the runtime.
