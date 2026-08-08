# 07 — Conversation memory

## Why "memory" has to be built

Chapter 01's key fact: the LLM API is **stateless** — every request stands
alone. When the assistant "remembers" that you asked about billing two
messages ago, that's only because our application stored the conversation
and **resent it** inside the next prompt. No storage on our side = total
amnesia between messages.

## Short-term memory: the transcript in Redis

**In this project:**
[`memory/session.py`](../../src/assistant/memory/session.py) — each session is
a Redis list of JSON messages (plus a TTL refreshed on every write, so
abandoned sessions expire). The WebSocket layer appends the user message and
the final answer each turn; reconnecting with the same `session_id` resumes
the conversation — even across a server restart, because the state lives in
Redis, not in process memory.

## The growth problem

Resending history has a nasty property: prompts grow every turn, so **cost
and latency grow with conversation length**, until the context window caps
you entirely. A 100-turn conversation replayed verbatim is slow, expensive,
and mostly irrelevant to the current question.

## The fix: rolling summarization

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
2. Under the budget (`ASSISTANT_HISTORY_CHAR_BUDGET`, default ~8000 chars ≈
   2k tokens)? → send summary + tail as-is. Done.
3. Over budget? **Fold**: everything except the last
   `ASSISTANT_HISTORY_KEEP_RECENT` messages is summarized *into* the
   existing summary; the `covered` index advances; both persist to Redis.
4. The agent then sees:
   `[system: "Summary of the earlier conversation: …"] + recent turns`.

Two properties worth pointing at:

- **Incremental** — each message is summarized exactly once, ever (the
  `covered` index guarantees it). No re-summarizing the whole history each
  turn.
- **Bounded** — prompt size stops growing. Our test proves it across all
  three backends: with a tiny budget, the model-visible message count pins
  at exactly `system + summary + recent + current` no matter how long the
  chat runs ([`tests/test_ws.py`](../../tests/test_ws.py) →
  `test_long_conversations_stay_bounded_by_summary`).

## Who writes the summary?

[`memory/summarizer.py`](../../src/assistant/memory/summarizer.py), two
strategies behind one protocol:

- **ExtractiveSummarizer** (offline default): deterministic one-line digests
  of the folded turns, capped — zero cost, perfect for tests/demo.
- **LLMSummarizer** (real providers): asks the model to maintain a factual
  running summary ("keep every fact, decision, name and number; ≤200
  words") — in production you'd point this at a cheap fast model, since
  summarization doesn't need the flagship.

## Long-term memory (the roadmap answer)

Rolling summaries are *within-session*. Cross-session memory ("this user
works on billing-service") is a different pattern: distill stable facts and
store them in the **vector DB**, retrieved like RAG when relevant. The
infrastructure for it (embedding, Qdrant, retriever) already exists in this
project; wiring a facts store is future work — say exactly that.

## Questions you might get

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
