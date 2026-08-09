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
[backend comparison](../reference/backend-comparison.md). The custom
loop's rule: **max 6 tool-loop iterations**, then it answers with what it
has; every backend gets the identical tool registry and system prompt.

## The WebSocket protocol (`/chat`)

Typed frames, defined in [api/schemas.py](../../src/assistant/api/schemas.py)
and mirrored in [frontend/src/types.ts](../../frontend/src/types.ts).

**Client → server:**

| frame | meaning |
|---|---|
| `{"type": "user_message", "content": "..."}` | ask a question (max 8000 chars) |
| `{"type": "cancel"}` | stop the turn in flight; ignored when nothing is running |

A second `user_message` while one is still answering is refused with an
`error` frame rather than queued — the model is mid-answer and the history a
queued question would be answered from is already stale.

**Server → client, in order per turn:**

| frame | fields | when |
|---|---|---|
| `session` | `session_id` | once, on connect (client stores it; reconnects pass `?session_id=` to resume) |
| `token` | `content` | each streamed answer piece |
| `tool_call` | `tool`, `arguments` | agent invokes a tool (UI shows a card) |
| `tool_result` | `tool`, `result` | tool finished (fills the card) |
| `final` | `content` | the complete answer (authoritative text) |
| `turn` | `turn_id, backend, duration_ms, first_token_ms, llm_steps, tool_calls[], prompt_tokens, completion_tokens, usage_estimated, cost_usd, cancelled` | **always last** — the stats line, on the stopped path too |
| `error` | `message` | anything failed; the socket stays open, mapped to friendly text (chapter 04) |

Connection query params: `?session_id=` (resume), `?backend=` (runtime),
`?token=` (auth — browsers can't set WS headers). Invalid JSON in → an
`error` frame, socket survives.

### Stopping a turn

The receive loop cannot read a `cancel` frame while it is awaiting the
answer, so each turn runs as its own `asyncio.Task` and the loop stays free
to read. `cancel` cancels that task; the exception lands inside `async for
event in agent.run(...)`, and leaving the loop closes the async generator —
which runs its `finally` blocks, ending spans and releasing the provider's
HTTP stream. Nothing is left dangling and no separate cleanup path exists to
drift.

A stopped turn is **not** an error:

- everything already streamed stays on screen, and is stored as history with
  a `[stopped by the user]` marker — otherwise the next turn would answer the
  same question from scratch,
- the tokens really were spent, so the summary and the audit record are
  written as usual, with `cancelled: true`,
- `assistant_cancelled_turns_total{backend}` counts it, and it is left out of
  the turn-duration histogram: a stopped turn measures the user's patience,
  not the system's latency.

Closing the tab does the same thing — the connection's `finally` cancels a
turn still in flight, so nobody pays for an answer no one is reading.

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
| `session:{id}:messages` | the transcript (list of ChatMessage JSON) |
| `session:{id}:summary` | the rolling summary |
| `session:{id}:turns` | audit records (last 50; chapter 07) |
| `sessions:index` | sorted set of session ids, scored by last activity |

`fakeredis://` gives the identical API in-process — sessions just die with
the server.

`sessions:index` is what makes the **Chats** panel possible without a
`KEYS`/`SCAN` sweep of the keyspace — the one access pattern that gets slower
exactly as a deployment gets busier. It is updated in the same round trip as
the message it records, so the index cannot outlive its data by a failed
second call. Redis expires keys but not sorted-set members, so `recent()`
also drops entries past the TTL and skips any session whose history is
already gone.

| endpoint | purpose |
|---|---|
| `GET /api/sessions` | recent conversations: id, last activity, message count, opening-question preview |
| `GET /api/sessions/{id}/messages` | the stored transcript — how the UI repaints a conversation you reopen |
| `DELETE /api/sessions/{id}` | forget one conversation: history, summary and audit trail |

All three are auth-guarded when `ASSISTANT_AUTH_TOKEN` is set: unlike
`/api/info` and `/api/health`, they return conversation content. Reopening a
conversation restores the transcript over HTTP and reconnects the socket with
`?session_id=` — the WebSocket resumes history for the *model* but never
replays it, so without that fetch a reopened chat would look empty while the
assistant plainly remembered it.

## Where the turn logic lives

Two objects, deliberately split.

[`api/ws.py`](../../src/assistant/api/ws.py) `_handle_turn` is the
**conductor**: it owns the socket, the `agent.turn` span, the error mapping
and persistence. It builds the bounded context, streams agent events to the
client, sends the `turn` frame, writes `turn.summary`, stores the audit
record — and converts any exception into a friendly `error` frame without
killing the socket.

[`api/turn_recorder.py`](../../src/assistant/api/turn_recorder.py)
`TurnRecorder` owns the **accounting**: feed it each event with `observe()`,
then ask for a `summary()` (the `turn` WS frame) and a `record()` (the
persisted audit row). It touches neither socket nor Redis, so the maths —
first-token latency, tool list, token totals, cost — is unit-testable
without a live WebSocket.

That split exists because the two used to be one 154-line function in which
every new event kind or metric meant editing the same block.
