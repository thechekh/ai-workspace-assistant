# 07 — Conversation memory

**What this chapter answers: why a stateless LLM API needs a memory layer of
its own, how this project keeps the full transcript durable in Redis while
bounding what the model actually sees, and who writes the rolling summary
that makes that possible.** It does not cover the wire format those messages
travel over between browser and server — that is
[08 — Real-time & WebSockets](08-realtime-websockets.md).

## 1. Why "memory" has to be built

Chapter 01's key fact: the LLM API is **stateless** — every request stands
alone. When the assistant "remembers" that you asked about billing two
messages ago, that's only because our application stored the conversation
and **resent it** inside the next prompt. No storage on our side = total
amnesia between messages.

## 2. Short-term memory: the transcript in Redis

**In this project:**
[`memory/session.py`](../../src/assistant/memory/session.py) — each session is
a Redis list of JSON messages (plus a TTL refreshed on every write, so
abandoned sessions expire). The WebSocket layer appends the user message and
the final answer each turn; reconnecting with the same `session_id` resumes
the conversation — even across a server restart, because the state lives in
Redis, not in process memory.

## 3. The growth problem

Resending history has a nasty property: prompts grow every turn, so **cost
and latency grow with conversation length**, until the context window caps
you entirely. A 100-turn conversation replayed verbatim is slow, expensive,
and mostly irrelevant to the current question.

## 4. The fix: rolling summarization

The standard pattern, implemented in
[`memory/conversation.py`](../../src/assistant/memory/conversation.py):

```
full transcript (Redis, never modified — the audit trail)
│
├── [0 .. covered)   already folded into a persisted rolling summary
└── [covered .. end) the "pending" tail, replayed verbatim
```

Every turn, before calling the agent:

1. Compute the size of the pending tail (chars as a cheap token proxy).
2. Under the budget (`ASSISTANT_HISTORY_CHAR_BUDGET`, default 8000 chars ≈
   2k tokens — [`config.py`](../../src/assistant/config.py))? → send summary
   + tail as-is. Done.
3. Over budget? **Fold**: everything except the last
   `ASSISTANT_HISTORY_KEEP_RECENT` messages (default 6) is summarized *into*
   the existing summary; the `covered` index advances; both persist to Redis.
4. The agent then sees:
   `[system: "Summary of the earlier conversation: …"] + recent turns`.

A worked example, from the test suite rather than a description of one:
[`tests/test_ws.py::test_long_conversations_stay_bounded_by_summary`](../../tests/test_ws.py)
configures a deliberately tiny budget (`history_char_budget=60`,
`history_keep_recent=2`, against the real defaults of 8000/6 above) and sends
five user messages in a row, on all three agent backends at once. `FakeLLM`
echoes back exactly how many messages it was handed, so the count itself is
the proof: by the fourth and fifth turn folding has fired on every
intervening turn and the model-visible count has stopped growing, pinned at
`(5 messages in context)` for both. The same durability claim has its own
proof in
[`test_history_persists_across_reconnects`](../../tests/test_ws.py): one
exchange over a WebSocket (context lands at `(2 messages in context)`), the
socket closes completely, a fresh one reconnects with `?session_id=…`, and
the second turn's context jumps straight to `(4 messages in context)` — the
first exchange came back from Redis, not from a process that happened to
still be running.

Two properties worth pointing at:

- **Incremental** — each message is summarized exactly once, ever (the
  `covered` index guarantees it). No re-summarizing the whole history each
  turn.
- **Bounded** — prompt size stops growing. The test above proves it across
  all three backends: with a tiny budget, the model-visible message count
  pins at exactly `system + summary + recent + current` no matter how long
  the chat runs.

## 5. Who writes the summary?

[`memory/summarizer.py`](../../src/assistant/memory/summarizer.py), two
strategies behind one protocol:

| Strategy | When used | How | Cost |
|---|---|---|---|
| `ExtractiveSummarizer` | `fake` provider (offline default) | Deterministic one-line digest per folded message, capped at the last 12 lines | $0 — string joins only |
| `LLMSummarizer` | Real providers | Asks the model to maintain a factual running summary ("keep every fact, decision, name and number; ≤200 words") | One completion per fold — point it at a cheap model in production, since summarizing doesn't need the flagship |

## 6. Long-term memory (the roadmap answer)

Rolling summaries are *within-session*. Cross-session memory ("this user
works on billing-service") is a different pattern: distill stable facts and
store them in the **vector DB**, retrieved like RAG when relevant. The
infrastructure for it (embedding, Qdrant, retriever) already exists in this
project; wiring a facts store is future work — say exactly that, and say why
it stayed future work rather than a quick add: a wrongly extracted fact
would poison every later conversation, and shipping that safely needs
provenance, TTLs and a correction path this POC does not have
([project/future-tools.md](../project/future-tools.md)).

## 7. Questions you might get

**"Doesn't summarizing lose information?"** — Yes, deliberately — that's
the trade. What's controlled: recent turns stay verbatim (the model sees the
immediate context exactly), the summary is instructed to keep facts and
decisions, the budget is a config knob, and the **full transcript is never
modified** in Redis — nothing is lost from the record, only from the prompt.

**"Why characters instead of tokens?"** — Honest approximation: ~4 chars ≈
1 token, and counting chars is free while exact token counting requires the
model's tokenizer per provider. For a budget threshold, the approximation
error is irrelevant; the constant is tuned accordingly.

**"Why is the summary a *system* message?"** — It's context *about* the
conversation, not a turn someone spoke. All three backends map it into their
native shape (tested), and models treat system content as reliable
background rather than something the user just said.

**"Why not just use a framework's built-in memory?"** — Pydantic AI and
LangGraph each bring their own memory/checkpoint story — it's literally one
of the axes [project/tech-stack.md](../project/tech-stack.md) compares the
three agent runtimes on — but only one implementation should decide what the
model sees, or three backends could bound history three different ways.
`ConversationMemory` builds the context once, outside all three
([05 — Agent frameworks](05-agent-frameworks.md)), and every backend
receives the same finished list regardless of which one is running.

## 8. Reading it honestly

- **A summary is a lossy compression of the truth, on trust.**
  `LLMSummarizer` is instructed to keep facts and numbers, but instructions
  are not a guarantee — nothing verifies a fold against the original text
  the way retrieval's recall@k verifies a search. A wrong summary silently
  becomes the model's only memory of the folded turns.
- **The bounding guarantee has exactly one shot at being caught if it
  breaks.** It holds because
  `test_long_conversations_stay_bounded_by_summary` passes — one assertion
  inside the 573-test offline suite (2026-09-04, `uv run pytest -q`). If
  that test were ever weakened, nothing else here would notice prompts
  silently growing again.
- **The char-per-token approximation drifts by provider and language.** ~4
  chars/token is close enough for English prose to size a budget; it is not
  a token count, and a knob tuned against it is tuned against an
  approximation of an approximation.
- **This is within-session only.** Nothing here carries a fact from one
  session into the next — that is the long-term memory gap named in §6, and
  it stays a named gap rather than a half-built feature.
- **`ExtractiveSummarizer` proves the mechanism, not the quality bar.** It
  proves folding and bounding work; it says nothing about whether an
  LLM-written summary in production actually preserves what mattered later
  — that would need a real model and a judge, and nothing in this repository
  currently checks it.

## 9. Related

- [08 — Real-time & WebSockets](08-realtime-websockets.md) — the frame protocol these messages travel over between browser and server
- [05 — Agent frameworks](05-agent-frameworks.md) — why one `AgentBackend` contract sits in front of three runtimes, memory included
- [handbook/08 — Agents, memory & WebSocket](../handbook/08-agents-memory-ws.md) — how to run this and watch a summary fold happen
- [project/future-tools.md](../project/future-tools.md) — the long-term memory facts store, and why it is deferred rather than half-built
- [11 — Glossary](11-glossary.md) — Session, Transcript, Rolling summary and Context budget, one line each
