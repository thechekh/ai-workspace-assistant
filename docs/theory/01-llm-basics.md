# 01 — LLM basics

**What this chapter answers: what an LLM actually computes, why tokens and
the context window bound everything built on top of one, and why this
project runs on a scripted fake provider by default.** It does not cover how
retrieved knowledge gets into the prompt — that is [03-rag.md](03-rag.md).

## 1. What a large language model actually does

An LLM (large language model) is a neural network trained on enormous
amounts of text to do one thing: **predict the next token** given everything
that came before. "The capital of France is ___" → the model assigns
probabilities to every possible next token, and "Paris" wins. Generating a
whole answer is just repeating that prediction, one token at a time, feeding
each generated token back in.

Everything impressive an LLM does — answering questions, writing code,
deciding to call a tool — emerges from that single mechanism at scale.

## 2. Tokens

Models don't read characters or words; they read **tokens** — chunks of text
from a fixed vocabulary. Roughly: 1 token ≈ 4 characters ≈ ¾ of an English
word. "deployment" might be one token; "Kubernetes" might be three.

Why you must care:

- **Pricing** is per token (e.g. "$0.15 per million input tokens").
- **Limits** are in tokens: the context window and the max output.
- **Latency** scales with tokens generated.

## 3. The context window

The context window is the model's **entire working memory for one request**
— system instructions, conversation history, retrieved documents, tool
results, and the answer being generated must all fit (typically 128k–1M
tokens on modern models). Nothing outside the window exists for the model.
Two consequences drive this project's design:

1. Long conversations must be **summarized** or truncated → chapter 07.
2. You can't "upload all the docs" — you retrieve only the relevant few
   chunks per question → chapter 03 (RAG).

## 4. The chat API

Every provider exposes roughly the same HTTP API (popularized by OpenAI).
You send a list of **messages**, each with a role:

```json
{
  "model": "gpt-4.1-nano",
  "messages": [
    {"role": "system", "content": "You are the AI Workspace Assistant..."},
    {"role": "user", "content": "Which service creates invoices?"},
    {"role": "assistant", "content": "billing-service does..."},
    {"role": "user", "content": "Who owns it?"}
  ],
  "stream": true
}
```

Four roles appear, and each means something different to the loop that reads
it back (this is the exact shape this project's
[`ChatMessage`](../../src/assistant/agent/base.py) uses):

| Role | Appears | Carries |
|---|---|---|
| `system` | once, first in the array | the operator's instructions: persona, rules, what tools exist |
| `user` | every user turn | the human's message |
| `assistant` | every model turn | reply text, or a `tool_calls` array when it chooses to act instead ([chapter 04](04-tool-calling-and-agents.md)) |
| `tool` | right after a tool runs | that tool's result, tagged with the `tool_call_id` it answers |

Note `arguments` inside a `tool_calls` entry is a JSON **string** the model
wrote, not a parsed object — chapter 04 covers what happens when it is
malformed.

## 5. The API is stateless — the most important fact in this file

The provider remembers **nothing** between requests. Every request must
resend the entire conversation. "Memory" is always an illusion your
application builds by storing history and replaying it (chapter 07). This is
why: chat costs grow with conversation length, and why summarization exists.

## 6. Streaming

Because generation is token-by-token, the API can send tokens **as they are
produced** (server-sent chunks) instead of waiting for the full answer.
That's the difference between a 6-second blank screen and text appearing
immediately. Our whole pipeline is streaming end-to-end: provider → agent
loop → WebSocket → browser (chapter 08).

## 7. Providers, and why we're provider-agnostic

Many companies serve models behind the *same* API shape ("OpenAI-compatible"):
OpenAI itself, Ollama (models on your own laptop, no key), Google's Gemini
compatibility endpoint, and most hosted-inference vendors. Same request
format — only the base URL, key, and model name differ.

**In this project:** [`llm/client.py`](../../src/assistant/llm/client.py) —
one `OpenAICompatibleLLM` class covers all hosted providers via a base-URL
map, so switching provider is a `.env` change, never a code change. The
`LLMClient` protocol (`stream_step(messages, tools)`) is the seam the rest
of the system depends on.

## 8. FakeLLM — the design decision worth defending

The default provider is `fake`: a deterministic offline stand-in that echoes
messages (reporting prompt size) and plays simple keyword heuristics for
tool use. It costs nothing, needs no key, and always behaves the same.

A worked example from the actual routing in
[`llm/fake.py`](../../src/assistant/llm/fake.py) — the module both `FakeLLM`
([`llm/client.py`](../../src/assistant/llm/client.py)) and the Pydantic AI
`FunctionModel` twin (chapter 05) import, precisely so the two cannot
quietly drift apart the way their hand-maintained predecessors already had.
One user message triggers exactly one of these, checked in priority order:

1. mentions a PR / pull request → `github__list_pull_requests`
2. the text "search code" (optionally "search code for X") → `code__search_code(pattern=X)`
3. any `http(s)` URL in the message → `fetch_url(url=...)`
4. a question ending in `?` → `search_docs(query=...)`
5. otherwise → a plain echo reply: `"[fake-llm] ({message_count} messages in
   context) You said: {user_text}"` — the message count is what the memory
   tests assert on.

[`tests/test_fake_parity.py`](../../tests/test_fake_parity.py) pins the
consequence: the same prompt must route to the same tool on **every** one of
the three agent backends (chapter 05) — exactly the guarantee a hand-copied
version per backend had already broken once before this module existed.

Why this is good engineering, not a shortcut:

- It separates two questions people usually blur: **"does the plumbing
  work?"** (sessions, streaming, tool loop, retrieval — testable
  deterministically) from **"is the model smart?"** (a model-quality
  question you evaluate separately).
- All **573 tests** run in seconds, offline, at $0 — no flaky network, no
  burnt quota, no nondeterministic assertions
  (`uv run pytest -q -p no:cacheprovider`, 2026-09-05).
- The demo works on a train. With an OpenAI key it becomes a real model with
  zero code changes.

`fake` was not the only free option on the table. The alternatives, and why
each lost, from [tech-stack.md](../project/tech-stack.md):

| Alternative | Why it lost |
|---|---|
| **Groq** free tier | Used first for real tool-calling development, then retired — superseded once `fake` covered offline dev/CI and OpenAI `gpt-4.1-nano` covered real answers; kept only as the reason the provider-hardening in `llm/client.py` exists |
| **Ollama** (local model) | Free and unlimited, but small local models are noticeably weaker at tool calling — fine for exercising the plumbing, not for judging agent quality |
| **Google Gemini** free tier | Generous quota and good tool calling; kept in reserve as "the second option if OpenAI limits bite," never the default |
| **OpenRouter** `:free` variants | One API over many providers, handy for ad-hoc model comparisons; never adopted as the standing dev provider |

## 9. Questions you might get

**"Which model do you use?"** — Any OpenAI-compatible one; it's a config
value, and there are deliberately just two modes in practice: a deterministic
offline fake for development and the whole test suite, and `gpt-4.1-nano` —
the cheapest OpenAI model that still calls tools reliably — for real answers.
Stepping up to `gpt-4o-mini` or `gpt-4.1-mini` is one line. The architecture
doesn't care.

**"Do LLMs understand what they say?"** — Functionally they model language
statistically. For this project the philosophical question doesn't matter:
we treat the model as an untrusted text-and-decisions generator, verify its
tool calls against schemas, and ground its answers in retrieved documents.

**"What about hallucinations?"** — Unconstrained models make things up
confidently. Our two mitigations: RAG (answers grounded in retrieved doc
chunks the user can inspect in the tool card — chapter 03) and a system
prompt instructing the model to say so when the docs don't cover something.

## 10. Reading it honestly

- **The fake provider proves plumbing, not model quality.** All 573 tests
  passing offline says the tool loop, streaming and memory work; it says
  nothing about whether a real model reasons well. That question is
  answered separately, on demand, and only when a key exists —
  [reference/ragas.md](../reference/ragas.md) is the measured version of it.
- **"Provider-agnostic" means OpenAI-compatible, not universal.** The chat
  API shape in this chapter covers OpenAI, Ollama, Gemini's compatibility
  endpoint and most hosted inference for free — it does not cover a
  materially different wire format like Anthropic's native API without new
  code. [project/future-tools.md](../project/future-tools.md) prices that
  addition at about half a day behind the same `InstrumentedLLM` seam, which
  is another way of saying it isn't free today.
- **The heuristics in `llm/fake.py` are simple on purpose.** They route on
  keywords — "search code", a trailing "?", a bare URL — not on intent. A
  real model's tool-choice reasoning is never exercised by an offline test,
  by design; see the worked example above.
- **Context-window and pricing figures are vendor numbers, not measured
  ones.** This project quotes what OpenAI publishes for its own models; it
  does not independently verify token limits or context sizes the way it
  verifies its own retrieval and faithfulness numbers.
- **Streaming hides a hardening layer this chapter doesn't cover.** Real
  providers retry, salvage malformed tool calls, and back off on 429s inside
  [`llm/client.py`](../../src/assistant/llm/client.py) — none of that is
  visible from the fake provider, and a framework that bypasses this client
  (chapter 05) has to re-earn it from scratch.

## 11. Related

- [02-embeddings-and-vector-search.md](02-embeddings-and-vector-search.md) — what happens to text once it has to be compared for meaning, not just generated
- [03-rag.md](03-rag.md) — why the context window forces retrieval instead of pasting every document into the prompt
- [07-memory.md](07-memory.md) — how a stateless API turns into a conversation that remembers
- [../handbook/04-llm-models-tokens.md](../handbook/04-llm-models-tokens.md) — providers, retries, usage and cost as this project actually wires them
- [../reference/backend-comparison.md](../reference/backend-comparison.md) — the fake provider proven identical across all three agent backends
