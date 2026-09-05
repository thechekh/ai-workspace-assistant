# 08 — Agents, memory & the WebSocket protocol

**What this chapter covers: the one contract all three agent runtimes
implement, the WebSocket wire protocol and how a turn can be stopped
cleanly, rolling conversation memory, and where the turn's own bookkeeping
lives in the code.** It is not the measured comparison of the three
runtimes — lines of code, latency, streaming behaviour, debuggability — for
that, see [reference/backend-comparison.md](../reference/backend-comparison.md).

## 1. The agent contract (one interface, three runtimes)

Every backend implements the same tiny protocol
([agent/base.py](../../src/assistant/agent/base.py)):

```python
class AgentBackend(Protocol):
    def run(self, history: list[ChatMessage], user_message: str) -> AsyncIterator[AgentEvent]:
        """Stream agent events for one user turn.

        `history` is the prior conversation (without the current message);
        the backend is responsible for composing the full prompt. The stream
        must end with a FinalEvent (or ErrorEvent).
        """
        ...
```

…where `AgentEvent` is `TokenEvent | ToolCallEvent | ToolResultEvent |
FinalEvent | ErrorEvent`. The WS layer and frontend consume events and never
know which runtime produced them — that's what makes the per-session
switcher possible (`?backend=` / the UI dropdown; unknown names fall back to
the default; history carries over because it lives in Redis, not the agent).

| Backend | File | What it is | Notable |
|---|---|---|---|
| `custom` *(default)* | [backends/custom.py](../../src/assistant/agent/backends/custom.py) | Hand-written ReAct loop over the LLM client | The reference: smallest, fully instrumented via `InstrumentedLLM` |
| `pydantic_ai` | [backends/pydantic_ai.py](../../src/assistant/agent/backends/pydantic_ai.py) | Pydantic AI `Agent` | Runs its **own** model layer (reports the run's usage into the shared turn stats); tools adapted via `Tool.from_schema` → still hit `Tool.run` |
| `langgraph` | [backends/langgraph.py](../../src/assistant/agent/backends/langgraph.py) | LangGraph state graph | Wraps our LLM client as a LangChain chat model; checkpointing in-memory (Redis saver is backlog) |

Measured comparison (code size, latency, behavior parity):
[backend comparison](../reference/backend-comparison.md). The custom
loop's own source is the plainest worked example of the contract: a `for _
in range(self._max_iterations)` loop (`max_iterations: int = 6` by default)
that streams `TextDelta`s as `TokenEvent`s, and — if the model asks for no
tools on a step — yields exactly one `FinalEvent(content=text)` and returns.
Exhausting all six iterations without a final answer yields the literal text
*"I hit the tool-call limit for one turn without reaching a final answer.
Please rephrase or narrow the question."* — every backend gets the
identical tool registry and system prompt, so this is a property of the
turn, not of any one runtime.

![One question sent to all three backends against the real model: turn id, steps, tools, tokens, first-token latency, total latency, cost, and the first line of each answer](../images/backend-comparison-turns.png)

Line by line, from the real run on 2026-09-05
([reference/backend-comparison.md §5](../reference/backend-comparison.md)):

- **The same question, three times** — *"Which files describe how todometer
  is released?"*, once per backend, against `gpt-4.1-nano`.
- **`steps 2`, `tools search_docs`, on every row** — all three backends made
  the same decision from the identical registry and system prompt: one
  search, then an answer.
- **`prompt 8980 / 8932 / 8980`** — near-identical prompt sizes; the small
  difference on Pydantic AI is its own message formatting.
- **`cost $0.000921 / $0.000918 / $0.000934`** — within two ten-thousandths
  of a cent of each other. Which contract implementation answered is not
  where the money goes.
- **`first tok 4481 / 2999 / 2239 ms`** — one sample each, so read this as
  "same order of magnitude", not a ranking.

## 2. The WebSocket protocol (`/chat`)

Typed frames, defined in [api/schemas.py](../../src/assistant/api/schemas.py)
and mirrored in [frontend/src/types.ts](../../frontend/src/types.ts).

**Client → server:**

| frame | meaning |
|---|---|
| `{"type": "user_message", "content": "..."}` | ask a question (max 8000 chars) |
| `{"type": "cancel"}` | stop the turn in flight; ignored when nothing is running |

A second `user_message` while one is still answering is refused with an
`error` frame rather than queued — the model is mid-answer and the history a
queued question would be answered from is already stale. Verbatim, from
[api/ws.py](../../src/assistant/api/ws.py): *"still answering the previous
message — stop it first, or wait for it to finish"*.

**Server → client, in order per turn:**

| frame | fields | when |
|---|---|---|
| `session` | `session_id` | once, on connect (client stores it; reconnects pass `?session_id=` to resume) |
| `token` | `content` | each streamed answer piece |
| `tool_call` | `tool`, `arguments` | agent invokes a tool (UI shows a card) |
| `tool_result` | `tool`, `result` | tool finished (fills the card) |
| `final` | `content` | the complete answer (authoritative text) |
| `turn` | `turn_id, backend, duration_ms, first_token_ms, llm_steps, tool_calls[], prompt_tokens, completion_tokens, usage_estimated, cost_usd, cancelled, failed` | **always last** — exactly one per turn, however it ended |
| `error` | `message` | anything failed; the socket stays open, mapped to friendly text (chapter 04) |

Connection query params: `?session_id=` (resume), `?backend=` (runtime),
`?token=` (auth — browsers can't set WS headers). Invalid JSON in → an
`error` frame, socket survives.

A `tool_result` event is not the tool's raw output: `truncate_for_event` in
[agent/base.py](../../src/assistant/agent/base.py) clips what reaches the UI
at `EVENT_RESULT_LIMIT = 1500` characters, marked with a trailing `…`. That
is a *display* limit, separate from and much smaller than the 20,000-character
cap that decides what the model itself sees
([handbook/06 §2](06-tools-mcp.md)) — the model always reasons over the full
capped result; only the tool card in the UI is shortened further.

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

### One `turn` frame per turn, always

A turn ends one of three ways, and all three finish with a `turn` frame:
completed, `cancelled: true`, or `failed: true` (preceded by the `error` frame
carrying the message). That makes the frame a usable end-of-turn marker — a
client can wait for exactly one of them without a timeout.

It is also an accounting rule. A turn that dies after the provider's retries
has already spent three prompts' worth of tokens; returning early on the error
path used to drop that from the summary, the audit trail and
`assistant_cost_usd_total` — hiding spend at exactly the moment spend is
worth watching. Failed and stopped turns are excluded from
`assistant_turn_seconds` (a provider timeout is not this system's latency) but
never from cost.

Proof, from [tests/test_review_regressions.py](../../tests/test_review_regressions.py):
`ExplodingLLM` streams `"here is the start of an ans"` and then raises
`RuntimeError("provider fell over mid-stream")`. `test_a_failed_turn_still_reports_what_it_cost`
asserts the frame sequence still ends in a `turn` frame with `failed: True`
and `completion_tokens > 0` — "the tokens spent before the failure are
real", as the test's own docstring puts it — and that the audited record
for that session shows `failed: True` too.
`test_the_partial_answer_of_a_failed_turn_survives_as_history` asserts the
partial text is stored as history ending `[answer interrupted]`, the
sibling marker to the cancelled path's `[stopped by the user]`.

## 3. Conversation memory (why prompts don't grow forever)

Full transcripts live in Redis (`SessionStore`, 24 h TTL) — but the model
never sees all of it. [`ConversationMemory`](../../src/assistant/memory/conversation.py)
builds each turn's context as:

```
system prompt + [rolling summary] + last N verbatim messages + new question
```

When the un-summarized tail exceeds `HISTORY_CHAR_BUDGET` (8000 chars ≈ 2k
tokens), everything but the last `HISTORY_KEEP_RECENT` (6) messages is
**folded into the summary** by the summarizer — `build_summarizer` in
[memory/summarizer.py](../../src/assistant/memory/summarizer.py) picks
`ExtractiveSummarizer` (deterministic, offline: keeps the last 12 one-line
digests) for the `fake` provider and `LLMSummarizer` (asks the configured
model to maintain a running summary, at most 200 words) otherwise. Result,
proven by test: after enough turns the prompt size *stops growing* —
identically on all three backends. The summary is stored per session in
Redis, so it survives reconnects and backend switches.

Concretely, from `tests/test_memory.py::test_over_budget_history_folds_into_summary`:
with `char_budget=100` and `keep_recent=2`, six roughly-30-character messages
collapse to a **3-message** context — one `system` summary message plus the
last two verbatim — and the fold is persisted (`covered` advances to `4` in
Redis) so the *next* call only folds whatever overflowed since, not the
whole history again (`test_folding_is_incremental_across_calls`). The same
mechanism, at the real 8,000-char / 6-message defaults, is what
`tests/test_ws.py::test_long_conversations_stay_bounded_by_summary` pins
end-to-end over the WebSocket, parametrized across all three backends.

## 4. Sessions & the audit trail in Redis

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
already gone — pinned by `tests/test_sessions_api.py::test_expired_sessions_are_not_listed`,
which deletes the message key directly (simulating the TTL firing) and
asserts `recent()` comes back empty.

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

## 5. Where the turn logic lives

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

There is a third, smaller step in the same conductor: before a `FinalEvent`
reaches the socket, `_handle_turn` rewrites it through
[`correct_unsupported_action_claims`](../../src/assistant/agent/output_guard.py)
— because the UI replaces the streamed text with `final.content` and that
same text is what gets stored as history, correcting it here (once, on the
seam every backend shares) corrects both what the user reads and what the
next turn's prompt contains.

![Four controls exercised offline: the output guard correcting a false erase claim, fetch_url refusing loopback and link-local addresses, code__read_file refusing a traversal, the rate limiter refusing a fourth turn](../images/security-controls.png)

Line by line (the full capture — this chapter cares about the first line,
the rest belong to [chapter 06](06-tools-mcp.md) and
[chapter 09](09-testing-operations.md)):

- **`1. output guard`** — the model's final text claimed *"All documents
  mentioning Qdrant have been permanently erased… Confirmed."* while having
  called only search tools. `_handle_turn` appended: *"Correction: I have no
  tool that can delete, edit or otherwise change existing data — I can only
  search, read, and add repository documentation when asked. Nothing was
  modified."* The line right after it shows the same guard leaving a *true*
  claim alone when `ingest_repo` really ran that turn —
  `KB_WRITE_TOOLS = {"ingest_repo"}` is the only tool name that can make a
  completion claim true.
- **`2. fetch_url(…)`** — the SSRF guard on the `fetch_url` *tool*, not the
  turn logic; see [chapter 06](06-tools-mcp.md).
- **`3. code__read_file('../../../etc/passwd')`** — the MCP path jail; also
  chapter 06.
- **`4. rate limit`** — three turns allowed, the fourth refused; the same
  limiter [chapter 09](09-testing-operations.md) covers operationally.

## 6. Showing it live

About ninety seconds, offline or real profile:

1. Open the same session in two tabs with `?backend=custom` and
   `?backend=pydantic_ai` (or use the UI dropdown) and ask the same
   question in each — *"identical event stream, identical tool registry,
   two different engines underneath — that's the whole point of the
   protocol."*
2. Mid-answer, press **Stop** — *"the turn still ends with exactly one
   `turn` frame, `cancelled: true`, and what's already on screen never
   disappears."*
3. Ask several short questions in a row until the stats line's prompt-size
   signal stops climbing — *"once the un-summarized tail passes the
   character budget, older turns fold into one summary message and the
   prompt stops growing — same mechanism at turn 4 as at turn 40."*
4. Ask the assistant to "delete everything in the knowledge base" — *"watch
   it refuse in one sentence; there is no tool that could do it, and the
   guard behind it would correct the claim even if the model tried."*

## 7. Reading it honestly

- **The switcher is per-connection, not mid-turn.** `?backend=` is read once,
  at connect; switching backends means reconnecting, which the UI does
  seamlessly, but a turn already streaming cannot change engines partway.
- **An unknown `?backend=` fails silently.** `test_backend_query_param_switches_runtime`
  pins this as intended behaviour (falls back to the default), but it also
  means a typo in the query string never surfaces as an error — only the
  `backend` field of the resulting `turn` frame reveals what actually ran.
- **Folding is lossy on purpose.** `ExtractiveSummarizer` keeps one-line
  digests, and `LLMSummarizer` is asked for "every fact, decision, name, and
  number that could matter later" in at most 200 words — a real
  conversation can exceed what 200 words can hold, and there is no way to
  ask the model which details it dropped.
- **In-memory checkpointing on the LangGraph backend is not durable.** A
  process restart loses any graph state that lived only in the
  `InMemorySaver`; conversation history survives in Redis regardless, but
  LangGraph's own resumability story does not apply here yet — a named,
  deliberately deferred item in
  [future-tools.md §4](../project/future-tools.md).
- **The output guard is a pattern match, not comprehension.** `_CLAIMED_MUTATION`
  in [output_guard.py](../../src/assistant/agent/output_guard.py) looks for
  specific phrasings of "I deleted/erased/updated…"; a differently worded
  false claim could still slip through. It backstops the system prompt, and
  the system prompt is what actually keeps this rare.

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `error: still answering the previous message — stop it first, or wait for it to finish` | a second `user_message` arrived while a turn was in flight | wait for the `turn` frame, or send `{"type": "cancel"}` first |
| Reopening a chat with `?session_id=` shows an empty transcript, but the model still remembers it | the client reconnected the socket but never called `GET /api/sessions/{id}/messages` | fetch the transcript over HTTP on reopen — the WebSocket resumes history for the model but never replays it to the client |
| `?backend=does_not_exist` answers normally instead of erroring | unknown backend names silently fall back to the configured default | check the `backend` field of the `turn` frame to see what actually ran |
| A long conversation's prompt-size figure stops climbing | `ConversationMemory` folded the tail into a rolling summary — this is the intended behaviour, not a bug | inspect `session:{id}:summary` in Redis, or the token counts in `turn.summary` log lines (chapter 07) |
| The assistant's answer claims a deletion or edit happened | the output guard should correct this; if the correction is missing, the phrasing did not match `_CLAIMED_MUTATION` | check [output_guard.py](../../src/assistant/agent/output_guard.py) and add the phrasing to `tests/test_review_regressions.py` |

## 9. Related

- [reference/backend-comparison.md](../reference/backend-comparison.md) — the three runtimes measured: lines of code, latency, streaming behaviour, debuggability
- [handbook/06 — Tools & MCP](06-tools-mcp.md) — `Tool.run`, the seam every backend's tool calls pass through
- [handbook/07 — Observability](07-observability.md) — how to watch a turn — logs, metrics, traces, stats — as it runs
- [reference/security.md](../reference/security.md) — the output guard and the rate limiter, as security controls rather than turn mechanics
- [tests/test_ws.py](../../tests/test_ws.py) — the WebSocket protocol claims in this chapter, each proved offline and parametrized across all three backends
