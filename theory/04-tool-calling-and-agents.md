# 04 — Tool calling & agents

## From answering to acting

A bare LLM can only emit text. **Tool calling** (also "function calling")
is the mechanism that lets it *do* things: search docs, query GitHub, run a
code search. The critical mental model:

> The model never executes anything. It **requests** a call by emitting
> structured JSON; **your application** executes it and feeds the result
> back. All power stays on your side of the line.

## The contract

You describe each tool to the model — name, natural-language description,
and a **JSON Schema** for its arguments:

```json
{
  "type": "function",
  "function": {
    "name": "search_docs",
    "description": "Search the internal engineering documentation ... Call this whenever the user asks about our systems, services, or processes.",
    "parameters": {
      "type": "object",
      "properties": {"query": {"type": "string"}},
      "required": ["query"]
    }
  }
}
```

The **description is prompt engineering** — it's how the model decides *when*
to use the tool. Instead of answering with text, the model may reply:

```json
{"tool_calls": [{"id": "call_1", "type": "function",
  "function": {"name": "search_docs",
               "arguments": "{\"query\": \"deployment architecture\"}"}}]}
```

Note `arguments` is a JSON **string** the model wrote — it must be parsed,
and it can be malformed. Your app runs the tool, then appends a message with
`role: "tool"` carrying the result, and calls the model again.

## The agent loop (ReAct)

An **agent** is just this loop — reasoning and acting alternately (the
pattern is called ReAct, from a 2022 paper). Watch the message array evolve
for one real question:

```
step 1  [system, user:"What is our deployment architecture?"]
        → model returns tool_calls: search_docs(query=...)
        [.. + assistant(tool_calls), tool:"[deployment.md — CI/CD] ArgoCD..."]
step 2  → model returns text: "Deploys are GitOps via ArgoCD... (deployment.md)"
        no tool_calls → that text is the final answer. Loop ends.
```

The loop terminates when a step produces no tool calls. Harder questions may
take several tool rounds; trivial ones take one step with zero calls.

**In this project:**
[`agent/backends/custom.py`](../src/assistant/agent/backends/custom.py) —
the whole loop is ~100 lines with no framework. Read it top to bottom once;
every agent framework is a wrapper around exactly this.

## The safety rails (each one is a test)

- **Iteration bound** — `max_iterations=6`. A confused model asking for
  tools forever ends with an explicit "hit the tool-call limit" answer, not
  an infinite loop.
- **Errors become results, not exceptions.** Unknown tool → the *model*
  receives `error: unknown tool 'restart_prod'` and gets to recover.
  A crashing tool → `error: tool failed: ...`. Malformed JSON arguments →
  empty dict, the tool responds with its own validation error. The loop
  never dies because the model misbehaved — the model is told what went
  wrong and usually self-corrects. ([`tests/test_tool_loop.py`](../tests/test_tool_loop.py)
  covers every branch with a scripted LLM.)
- **The registry is an allowlist.** The model can only request tools we
  registered ([`agent/tools.py`](../src/assistant/agent/tools.py)); there is
  no "run arbitrary code" escape hatch.

## Streaming events, not just text

The loop yields a typed event stream — `token`, `tool_call`, `tool_result`,
`final` ([`agent/base.py`](../src/assistant/agent/base.py)) — which the
WebSocket forwards verbatim and the UI renders as streaming text plus tool
cards. Users *see* the agent working: which tool, which arguments, what came
back. Transparency is a feature — it's also your live debugging view.

## Where do tools come from here?

Three sources, one registry, indistinguishable to the loop:

1. Native: `search_docs` over the RAG retriever (chapter 03).
2. MCP servers: `code__search_code`, `github__list_pull_requests` (chapter 06).
3. (Anything future — the registry is the single extension point.)

## Questions you might get

**"What stops the model from calling a dangerous tool?"** — It can only
request tools from our registry; execution is entirely server-side; every
argument passes through a JSON schema and the handler's own validation (e.g.
`read_file` rejects path traversal). Adding a destructive tool would be the
place to add human confirmation — by design, that gate lives in *our* loop,
not in the model.

**"How does the model know when to use a tool?"** — The tool description
plus the system prompt. That's steerable text: our prompt says "when a
question concerns our systems, call search_docs first". With the offline
fake we simulate that decision with keyword heuristics; a real model makes
it from the descriptions.

**"What if it picks the wrong tool or bad arguments?"** — It gets the error
back as a tool result and retries differently — same as a junior engineer
reading an error message. Bounded by max_iterations either way.

**"Agent vs chatbot — what's the difference?"** — A chatbot maps one input
to one text output. An agent runs a loop where the model chooses actions,
observes results, and iterates until done. The loop above *is* that
difference, in ~100 lines.
