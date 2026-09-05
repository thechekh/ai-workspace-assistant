# 08 — Real-time chat & WebSockets

**What this chapter answers: why chat needs a persistent bidirectional
connection instead of request/response, the typed frame protocol both sides
speak, and what happens to a turn already in flight when the client cancels
or disconnects.** It does not cover what rides inside those frames as
`history` — that bounding logic is [07 — Conversation memory](07-memory.md).

## 1. Why plain HTTP isn't enough

Classic HTTP is request → response: send a question, wait, get the full
answer. For LLM chat that means staring at a spinner for many seconds while
generation finishes. We want tokens on screen **as they're generated**
(chapter 01: generation is inherently incremental) plus live "the agent is
calling search_docs" updates.

The three options:

| Option | How | Fit |
|---|---|---|
| Polling | Client asks "anything new?" every second | Wasteful, laggy — no |
| SSE (server-sent events) | One HTTP response kept open, server streams down | Fine for one-way streams; a second channel needed for anything upstream |
| **WebSocket** ✅ | One persistent, **bidirectional** connection | One connection carries the whole conversation both ways; natural fit for chat sessions and future features (typing, cancel/interrupt) |

The task spec also names WebSocket explicitly (`ws://…/chat`), and it shaped
a decision one layer down too:
[project/tech-stack.md](../project/tech-stack.md) picked FastAPI + uvicorn
over Litestar and granian partly *because* `uvicorn[standard]` pulls in the
`websockets` library the chat endpoint needs — the protocol choice here was
made before the framework choice, not after.

## 2. Our protocol — typed frames, not loose JSON

Both sides exchange small JSON frames tagged by `type`. Defined once as
Pydantic models on the backend
([`api/schemas.py`](../../src/assistant/api/schemas.py)) and mirrored as
TypeScript types in the frontend
([`frontend/src/types.ts`](../../frontend/src/types.ts)):

```
client → server   {type: "user_message", content: "..."}   at most 8000 chars
                  {type: "cancel"}                         stop the turn in flight
server → client   {type: "session", session_id}        connection bootstrap
                  {type: "token", content}             one streamed piece
                  {type: "tool_call", tool, arguments} agent is acting
                  {type: "tool_result", tool, result}  what came back
                  {type: "final", content}             the finished answer
                  {type: "turn", turn_id, duration_ms,  per-turn stats, sent
                        llm_steps, tokens, cost_usd…}   right after `final`
                  {type: "error", message}             something failed
```

The `turn` frame is what the UI renders as the stats line under an answer
(duration, first-token latency, LLM steps, real-or-estimated tokens, cost,
tools used) — see [handbook 07](../handbook/07-observability.md).

The event stream is exactly what the agent loop emits (chapter 04) — the WS
layer ([`api/ws.py`](../../src/assistant/api/ws.py)) forwards it verbatim. The
UI renders tokens as they arrive, tool frames as cards, and reconciles the
streamed text with `final`.

A worked example of why the client side is a *discriminated* union rather
than loose JSON:
[`ClientMessage = Annotated[UserMessage | CancelRequest,
Field(discriminator="type")]`](../../src/assistant/api/schemas.py). Anything
that fails that validation never reaches the agent — `api/ws.py` catches the
`ValidationError` and replies with an `error` frame naming both legal
shapes, then reads the next frame instead of closing, proved directly by
[`test_invalid_payload_reports_error_and_keeps_socket_alive`](../../tests/test_ws.py).

## 3. Sessions and reconnects

The server issues a `session_id` on connect; the client persists it and
reconnects with `?session_id=...` — history comes back from Redis
(chapter 07), so a refresh or a backend-dropdown switch **continues the
same conversation**. The frontend WS client
([`frontend/src/stores/chat.ts`](../../frontend/src/stores/chat.ts), VueUse
`useWebSocket`) auto-reconnects with backoff — concretely,
`autoReconnect: { retries: 10, delay: 2000 }`, ten attempts two seconds
apart before the UI gives up and shows disconnected.

Proof that "resume" really means Redis, not a process that never restarted:
[`test_history_persists_across_reconnects`](../../tests/test_ws.py) sends one
message (`FakeLLM` reports `(2 messages in context)`), closes the WebSocket
completely, opens a fresh one with the same `?session_id=`, sends a second
message, and gets back `(4 messages in context)` — the first exchange was
reloaded from Redis and fed to the model again, across a connection the
server never held open in between.

Query params also carry `backend=` (per-session runtime switch, chapter 05)
and `token=` (auth, chapter 10 — browsers can't set headers on WS, hence
the query param).

## 4. Failure philosophy: the socket survives

A hard-won rule (found by a real outage during development, then encoded in
code and tests): **anything that fails while handling a message — LLM,
tool, Redis, Qdrant — becomes an `{type: "error"}` frame, and the loop keeps
serving.** The connection only closes when the client leaves or auth fails.
Malformed client frames get an error frame too, not a disconnect.

The `cancel` frame is the sharpest test of that philosophy, and the reason
the protocol has to be bidirectional at all: stopping a turn already
mid-answer needs the receive loop to stay free to read a frame while the
turn is still running, which is why `_handle_turn` runs as its own
`asyncio.Task` rather than inline
([`api/ws.py`](../../src/assistant/api/ws.py)). A `cancel` calls
`turn.cancel()`; the resulting `CancelledError` is caught, the partial
answer stays on screen and is saved to history as `"… [stopped by the
user]"` (so the next turn doesn't re-answer the same question from
scratch), and the tokens already spent are still counted —
`assistant_cancelled_turns_total` increments but is deliberately excluded
from the turn-latency histogram, because a stopped turn measures the user's
patience, not the system's. All of that is pinned by
[`test_cancelled_turn_is_audited_and_keeps_the_partial_answer`](../../tests/test_ws.py),
which cancels a turn mid-stream and asserts the audit record still shows
`cancelled: true` with the partial text retained.

## 5. Questions you might get

**"Why not SSE like most LLM demos?"** — SSE streams one way; chat is
two-way (messages up, tokens down) so SSE needs a POST side-channel per
message, splitting one conversation across many requests. One WS connection
*is* the session — simpler state model and room for interrupts/typing later.
For pure one-shot completion streaming, SSE would be the simpler right
answer; this is a session, not a one-shot.

**"How does this scale beyond one server?"** — WS connections are sticky by
nature; the state that matters (history, summary) is already externalized to
Redis, so any instance can serve any session — you scale with a WS-aware
load balancer and N stateless API pods. That separation was the point of
putting sessions in Redis from day one.

**"What about backpressure / slow clients?"** — Frames are tiny (tokens and
tool summaries — tool results are trimmed to 1500 chars for the UI while
the model sees the full text), and the async send naturally paces per
connection. At demo scale it's a non-issue; at production scale you'd add
per-connection send queues with drop/close policies.

**"Why does reopening an old conversation fetch over HTTP instead of the
WebSocket replaying it?"** — Because the socket's job is carrying the
*model's* context forward (chapter 07), not repainting the browser.
Reopening a stored conversation fetches the transcript over plain HTTP and
only then reconnects
([`frontend/src/stores/chat.ts`](../../frontend/src/stores/chat.ts)
`switchSession`) — otherwise reopening an old conversation would show an
empty window the assistant nonetheless remembered.

## 6. Reading it honestly

- **One turn at a time, per connection.** A second `user_message` while one
  is still running gets an `error` frame telling the client to wait or
  cancel first — there is no queueing of a second question behind the
  first. A deliberate simplicity trade, but a real limit of the protocol as
  shipped, not just of the UI.
- **No typing indicators, read receipts, or multi-user presence.** The
  bidirectional channel makes them possible later; none of them exist
  today.
- **Backpressure is reasoned about, not measured.** §5 states the
  production answer (per-connection send queues); nothing in this
  repository exercises a genuinely slow client, so the claim is engineering
  judgment, not a benchmark.
- **A closed tab still burns whatever the turn already spent.**
  Cancellation and disconnection both stop the stream, but tokens already
  generated were already paid for — the cost accounting says so honestly
  rather than hiding it (§4).
- **All of the above is pinned by tests, not just read from the code.**
  Session resume, the cancel path and the invalid-frame handling in §2–§4
  are each one assertion inside the 382-test offline suite (2026-09-04,
  `uv run pytest -q`) — the number this whole protocol has to keep passing
  against every time it changes.

## 7. Related

- [07 — Conversation memory](07-memory.md) — what actually rides inside these frames as `history`
- [04 — Tool calling & agents](04-tool-calling-and-agents.md) — the event stream this protocol forwards verbatim
- [handbook/08 — Agents, memory & WebSocket](../handbook/08-agents-memory-ws.md) — running it, with the full frame reference
- [handbook/07 — Observability](../handbook/07-observability.md) — reading the `turn` frame's stats line and the metrics behind it
- [11 — Glossary](11-glossary.md) — WebSocket, SSE, Cancellation and Backpressure, one line each
