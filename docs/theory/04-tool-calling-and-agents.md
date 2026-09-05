# 04 — Tool calling & agents

**What this chapter answers: how a model goes from emitting text to
triggering real code, what the loop around that looks like, and the rails
that keep a misbehaving model from doing damage.** It does not cover the
frameworks that can wrap this loop — see [05-agent-frameworks.md](05-agent-frameworks.md)
for that; this chapter is the mechanism underneath all three.

## 1. From answering to acting

A bare LLM can only emit text. **Tool calling** (also "function calling")
is the mechanism that lets it *do* things: search docs, query GitHub, run a
code search. The critical mental model:

> The model never executes anything. It **requests** a call by emitting
> structured JSON; **your application** executes it and feeds the result
> back. All power stays on your side of the line.

## 2. The contract

You describe each tool to the model — name, natural-language description,
and a **JSON Schema** for its arguments:

```json
{
  "type": "function",
  "function": {
    "name": "search_docs",
    "description": "Search the knowledge base: documents the team has added to this assistant ... Call this whenever the user asks about our systems, services, or processes.",
    "parameters": {
      "type": "object",
      "properties": {"query": {"type": "string"}},
      "required": ["query"]
    }
  }
}
```

(Abbreviated for the example — the real description also covers ingested
GitHub repositories and when to follow up with `repo_read_file`; see
[`agent/tools/search_docs.py`](../../src/assistant/agent/tools/search_docs.py)
for the exact text.)

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

## 3. The agent loop (ReAct)

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
[`agent/backends/custom.py`](../../src/assistant/agent/backends/custom.py) —
the whole loop is **98 lines** with no framework
(`wc -l src/assistant/agent/backends/custom.py`, 2026-09-04). Read it top to
bottom once; every agent framework (chapter 05) is a wrapper around exactly
this.

That's also a considered ordering, not an accident: [tech-stack.md](../project/tech-stack.md)
sequenced the work *custom loop → Pydantic AI → LangGraph* on purpose, so
that a framework's abstractions (chapter 05) always arrive after the raw
~100-line mechanism they wrap is already on the page — "no magic" is easier
to believe once you've read the whole thing once.

## 4. The safety rails (each one is a test)

Seven rails, each backed by a real test or the shape of the code itself —
the loop never dies because the model misbehaved; it gets told what went
wrong and usually self-corrects:

| Rail | What happens | Pinned by |
|---|---|---|
| Iteration bound | `max_iterations=6`; round 7 short-circuits to an explicit "hit the tool-call limit" answer instead of looping forever | `test_loop_stops_at_max_iterations` |
| Unknown tool | the model gets back `error: unknown tool 'restart_prod'`, not a crash | `test_unknown_tool_reports_error_and_recovers` |
| Malformed JSON arguments | parsed to `{}`; the tool's own validation reports what's missing | `test_malformed_json_arguments_degrade_to_empty_dict` |
| Crashing tool | becomes `error: tool 'x' failed: <exc>`; the loop continues | `test_crashing_tool_becomes_error_result_not_exception` |
| Oversized tool result | capped at 20,000 characters *before the model ever sees it*, with a truncation marker | `test_a_huge_tool_result_is_capped_before_the_model_sees_it` |
| Duplicate call | the same tool + arguments twice in one turn returns a pointer to the earlier result instead of re-running it | `test_fetch_url.py`'s duplicate-call assertion |
| Registry allowlist | the model can only request tools we registered; there is no "run arbitrary code" escape hatch | shape of [`agent/tools/base.py`](../../src/assistant/agent/tools/base.py) |

The first five rows live in
[`tests/test_tool_loop.py`](../../tests/test_tool_loop.py); the duplicate
guard is pinned in [`tests/test_fetch_url.py`](../../tests/test_fetch_url.py)
even though the guard itself is generic to every tool.

The 20,000-character cap isn't an arbitrary round number — the code comment
that sets it records why: *"measured live, one PR listing against a busy
repository came back as ~149,000 prompt tokens ($0.0154, 57x a normal
turn)"* — a real incident this project hit, not a guess.

## 5. Streaming events, not just text

The loop yields a typed event stream — `token`, `tool_call`, `tool_result`,
`final` ([`agent/base.py`](../../src/assistant/agent/base.py)) — which the
WebSocket forwards verbatim and the UI renders as streaming text plus tool
cards. Users *see* the agent working: which tool, which arguments, what came
back. Transparency is a feature — it's also your live debugging view.

## 6. Where do tools come from here?

Three sources, one registry, indistinguishable to the loop:

1. Native: `search_docs` over the RAG retriever (chapter 03), `fetch_url`,
   `repo_read_file`, and the one write, `ingest_repo`.
2. MCP servers: `code__search_code`, `github__list_pull_requests` (chapter 06).
3. (Anything future — the registry is the single extension point.)

Even inside "native," a routing choice was made deliberately rather than by
default: reading one file from a public GitHub repository could have gone
through the GitHub MCP server's own vendor `repos` toolset
(`get_file_contents` and friends) instead of a purpose-built native tool. It
didn't — the native `repo_read_file` is tokenless for public repositories,
while the vendor route needs a PAT for every single call and adds ten to
fifteen extra tool schemas to *every* prompt instead of one
([future-tools.md](../project/future-tools.md)).

## 7. Questions you might get

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
difference, in 98 lines.

## 8. Reading it honestly

- **The iteration bound can't tell "confused" from "legitimately hard."** A
  question that genuinely needs 7 searches gets the same "hit the tool-call
  limit" message as a model stuck in a loop — the rail is blunt by
  necessity, not because the two cases look the same to a human.
- **Duplicate-call detection is exact-match.** It compares tool name plus
  the JSON-serialized arguments; a model that rephrases the same query
  slightly pays for a second real call the guard was meant to prevent.
- **The 20,000-character cap protects budget, not correctness.** A
  truncated tool result can silently drop the one line that mattered — the
  model is told the cut happened, never what was inside it.
- **"Errors become results" assumes the model reads them.** Nothing forces
  a model to act correctly on an error string; a sufficiently confused model
  just spends its six iterations differently and still fails, more slowly.
- **The offline fake never exercises real tool-choice reasoning.** Its
  routing is keyword heuristics (chapter 01); whether a real model picks the
  right tool from the description alone is only checked when a real
  provider is configured, not by the 573-test default suite.

## 9. Related

- [03-rag.md](03-rag.md) — `search_docs`, the native tool this chapter's worked example calls
- [05-agent-frameworks.md](05-agent-frameworks.md) — this same loop, reimplemented on two frameworks and measured against it
- [06-mcp.md](06-mcp.md) — where the other tool source in the registry above comes from
- [../reference/tools.md](../reference/tools.md) — every tool's exact parameters, return shape and errors
- [../../tests/test_tool_loop.py](../../tests/test_tool_loop.py) — the scripted-LLM tests behind every safety rail in this chapter
