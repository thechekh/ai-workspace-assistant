# 01 — LLM basics

## What a large language model actually does

An LLM (large language model) is a neural network trained on enormous
amounts of text to do one thing: **predict the next token** given everything
that came before. "The capital of France is ___" → the model assigns
probabilities to every possible next token, and "Paris" wins. Generating a
whole answer is just repeating that prediction, one token at a time, feeding
each generated token back in.

Everything impressive an LLM does — answering questions, writing code,
deciding to call a tool — emerges from that single mechanism at scale.

## Tokens

Models don't read characters or words; they read **tokens** — chunks of text
from a fixed vocabulary. Roughly: 1 token ≈ 4 characters ≈ ¾ of an English
word. "deployment" might be one token; "Kubernetes" might be three.

Why you must care:

- **Pricing** is per token (e.g. "$0.15 per million input tokens").
- **Limits** are in tokens: the context window and the max output.
- **Latency** scales with tokens generated.

## The context window

The context window is the model's **entire working memory for one request**
— system instructions, conversation history, retrieved documents, tool
results, and the answer being generated must all fit (typically 128k–1M
tokens on modern models). Nothing outside the window exists for the model.
Two consequences drive this project's design:

1. Long conversations must be **summarized** or truncated → chapter 07.
2. You can't "upload all the docs" — you retrieve only the relevant few
   chunks per question → chapter 03 (RAG).

## The chat API

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

- **system** — the operator's instructions: persona, rules, what tools exist.
- **user / assistant** — the conversation turns.
- Two more appear with tools: an assistant message carrying **tool_calls**,
  and a **tool** message carrying a tool's result (chapter 04).

## The API is stateless — the most important fact in this file

The provider remembers **nothing** between requests. Every request must
resend the entire conversation. "Memory" is always an illusion your
application builds by storing history and replaying it (chapter 07). This is
why: chat costs grow with conversation length, and why summarization exists.

## Streaming

Because generation is token-by-token, the API can send tokens **as they are
produced** (server-sent chunks) instead of waiting for the full answer.
That's the difference between a 6-second blank screen and text appearing
immediately. Our whole pipeline is streaming end-to-end: provider → agent
loop → WebSocket → browser (chapter 08).

## Providers, and why we're provider-agnostic

Many companies serve models behind the *same* API shape ("OpenAI-compatible"):
OpenAI itself, Ollama (models on your own laptop, no key), Google's Gemini
compatibility endpoint, and most hosted-inference vendors. Same request
format — only the base URL, key, and model name differ.

**In this project:** [`llm/client.py`](../../src/assistant/llm/client.py) —
one `OpenAICompatibleLLM` class covers all hosted providers via a base-URL
map, so switching provider is a `.env` change, never a code change. The
`LLMClient` protocol (`stream_step(messages, tools)`) is the seam the rest
of the system depends on.

## FakeLLM — the design decision worth defending

The default provider is `fake`: a deterministic offline stand-in that echoes
messages (reporting prompt size) and plays simple keyword heuristics for
tool use. It costs nothing, needs no key, and always behaves the same.

Why this is good engineering, not a shortcut:

- It separates two questions people usually blur: **"does the plumbing
  work?"** (sessions, streaming, tool loop, retrieval — testable
  deterministically) from **"is the model smart?"** (a model-quality
  question you evaluate separately).
- All 344 tests run in seconds, offline, at $0 — no flaky network, no burnt
  quota, no nondeterministic assertions.
- The demo works on a train. With a OpenAI key it becomes a real model with
  zero code changes.

## Questions you might get

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
