# 08 — Real-time chat & WebSockets

## Why plain HTTP isn't enough

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

The task spec also names WebSocket explicitly (`ws://…/chat`).

## Our protocol — typed frames, not loose JSON

Both sides exchange small JSON frames tagged by `type`. Defined once as
Pydantic models on the backend
([`api/schemas.py`](../src/assistant/api/schemas.py)) and mirrored as
TypeScript types in the frontend
([`frontend/src/types.ts`](../frontend/src/types.ts)):

```
client → server   {type: "user_message", content: "..."}
server → client   {type: "session", session_id}        connection bootstrap
                  {type: "token", content}             one streamed piece
                  {type: "tool_call", tool, arguments} agent is acting
                  {type: "tool_result", tool, result}  what came back
                  {type: "final", content}             the finished answer
                  {type: "error", message}             something failed
```

The event stream is exactly what the agent loop emits (chapter 04) — the WS
layer ([`api/ws.py`](../src/assistant/api/ws.py)) forwards it verbatim. The
UI renders tokens as they arrive, tool frames as cards, and reconciles the
streamed text with `final`.

## Sessions and reconnects

The server issues a `session_id` on connect; the client persists it and
reconnects with `?session_id=...` — history comes back from Redis
(chapter 07), so a refresh or a backend-dropdown switch **continues the
same conversation**. The frontend WS client
([`frontend/src/stores/chat.ts`](../frontend/src/stores/chat.ts), VueUse
`useWebSocket`) auto-reconnects with backoff.

Query params also carry `backend=` (per-session runtime switch, chapter 05)
and `token=` (auth, chapter 10 — browsers can't set headers on WS, hence
the query param).

## Failure philosophy: the socket survives

A hard-won rule (found by a real outage during development, then encoded in
code and tests): **anything that fails while handling a message — LLM,
tool, Redis, Qdrant — becomes an `{type: "error"}` frame, and the loop keeps
serving.** The connection only closes when the client leaves or auth fails.
Malformed client frames get an error frame too, not a disconnect.

## Questions you might get

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
