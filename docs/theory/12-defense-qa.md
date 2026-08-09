# 12 — Defense Q&A

The questions most likely to come up, with answers that hold.

**General technique:** answer with a **decision → reason → evidence in the
repo**, and concede trade-offs proactively. The honest concession is what
makes the rest credible. Every number below is real and reproducible; if you
cite one, be ready to run the command.

**Numbers worth memorising**

| Claim | Value | How to prove it |
|---|---|---|
| Tests | 212 backend + 16 frontend | `uv run pytest -q`, `npm run test:run` |
| Coverage | 83.7%, floor enforced in CI | `uv run pytest --cov` |
| Retrieval quality | recall@1 **0.83**, recall@5 **1.00**, MRR **0.92** | `uv run python evals/run_retrieval.py --memory` |
| Agent backends | 98 / 194 / 278 lines, one protocol | `wc -l src/assistant/agent/backends/*.py` |
| Real-model cost | ~$0.012 for a full 8-case acceptance run | the stats line under every answer |
| Suite runtime | ~13 s, fully offline | no network, no Docker |

---

## Architecture

**Q: Why implement the agent three times? Isn't once enough?**
Because "which framework?" was a real open question and we wanted an
evidence-based answer, not a blog-post opinion. The marginal cost was low —
98/194/278 lines; everything else (tools, memory, telemetry, protocol) is
shared — and the payoff is [backend-comparison.md](../reference/backend-comparison.md)
with measured numbers, plus the choice stays reversible via one config value.
The comparison *is* a deliverable, not a detour.

**Q: Why a custom loop at all when frameworks exist?**
It's ~100 lines, it's the ground truth the frameworks wrap, and it makes
every debugging session easier because we know what "correct" looks like
underneath. It's also the teaching artifact for this workshop.

**Q: Why WebSockets instead of REST or SSE?**
Chat is bidirectional and session-shaped: one connection carries messages up
and tokens/tool-events/stats down, with session resume on reconnect. SSE
would need a POST side-channel per message. (The task spec also names
`ws://`.)

**Q: How does the abstraction actually stay honest?**
The WS test suite is parametrized across all three backends — identical
assertions, including memory-bounding. And after a regression where the
offline fake for one backend drifted from the others,
[test_fake_parity.py](../../tests/test_fake_parity.py) now asserts all three
route the same prompt to the same tool end-to-end. That bug is the reason
the test exists; say so.

**Q: What breaks first in production?**
Honest list: (1) a single shared bearer token → replace with OIDC at the
gateway; (2) no rate limiting or per-user quotas; (3) LangGraph's in-process
checkpointer → needs a Redis/Postgres saver to be durable; (4) no session
management UI. None are architectural — the stateful parts (Redis, Qdrant)
are already externalised, so API pods scale horizontally today.

---

## LLM & AI

**Q: Which model does this run on, and what does it cost?**
The provider is a **config value**, not a code decision: one
OpenAI-compatible client covers Groq, OpenAI, Ollama and Gemini, plus a
deterministic offline `fake` used as the dev/test default. The whole test
suite and all plumbing work runs at **$0**. Real-model work runs on Groq's
free tier (`llama-3.3-70b-versatile`); a full 8-case acceptance pass costs
about **$0.012** at listed prices — and the app *tells you*, per turn, in
the stats line and in `assistant_cost_usd_total`.

**Q: So is the cost real or estimated?**
Both, labelled. When the provider reports usage (Groq does, via
`stream_options.include_usage`) the numbers are real; otherwise we fall back
to a `chars/4` estimate and flag it `(est)` in the UI. Cost is priced from a
small per-model table — indicative at list prices, since the free tier
actually bills $0.

**Q: How do you prevent hallucinations?**
Three layers. **Grounding**: the agent retrieves chunks and answers from
them, citing source and heading. **Transparency**: the tool card shows the
user exactly what evidence was retrieved, and "details" replays the whole
turn. **Refusal paths**: the system prompt forbids stating the content of a
page it did not fetch, and the tools return explicit "nothing indexed" /
"nothing relevant" messages that instruct the model not to retry and to say
so. That last one came from a real failure — the model invented a plausible
description of a GitHub repo it had never fetched; `fetch_url` plus the
honesty instructions exist because of it.

*(Full threat model and control-by-control detail:*
[reference/security.md](../reference/security.md)*.)*

**Q: What about prompt injection — a document that says "ignore your instructions"?**
A real concern, and note the threat model *changed*: documents are now
uploaded at runtime, so the corpus is no longer necessarily trusted. What
bounds the blast radius is structural: the model can only call allowlisted,
**read-only** tools; execution is server-side; tool arguments are
schema-validated; `read_file` is jailed to the repo root (path-traversal
test in the suite); and `fetch_url` refuses loopback and private ranges.
What we have *not* built: content sanitisation on ingest and
tool-permission tiers. The `ToolRegistry` is the natural enforcement point
for both.

**Q: Real models are flaky. How did you handle that?**
This is where most of the hardening went, and all of it came from live
failures against Groq, not speculation:
- **429s** → backoff that honours `Retry-After` (including `retry-after: 0`,
  which a naive truthiness check silently discards — that was a real bug),
  and the provider's own message surfaced to the user, because per-minute
  and per-day limits need different advice.
- **`tool_use_failed`** → llama sometimes emits malformed tool-call JSON;
  the step is retried, then the call is salvaged from Groq's
  `failed_generation` payload.
- **Leaked tool syntax** → llama sometimes prints `<function.name>{…}` as
  *text*; that text is withheld, parsed into a real tool call, and never
  reaches the chat.
- **Repeated identical calls** → a per-turn duplicate guard; we measured the
  same URL being fetched three times in one turn, burning the rate budget.

---

## RAG

**Q: Why RAG and not fine-tuning?**
Facts change weekly; re-ingest is seconds and free, retraining is neither.
RAG cites sources, so answers are auditable; fine-tuned knowledge is opaque.
Fine-tuning is the tool for style and behaviour, not for living
documentation.

**Q: Walk me through the pipeline.**
Ingest: heading-aware Markdown chunking → dense embedding + sparse lexical
vector → upsert to Qdrant with deterministic ids (so re-ingesting replaces
in place). Query: embed the query → hybrid dense+sparse search with RRF
fusion → lexical rerank of the top-20 → relevance gate → top-4 chunks with
source, heading and score.

**Q: Your embedder isn't a real embedding model.**
Correct, and it's disclosed everywhere: `hash-512` is lexical feature
hashing — deterministic, $0, and good enough to build *and measure* the
whole pipeline. Semantic models (OpenAI `text-embedding-3-small`, voyage-3)
are a config switch plus a re-ingest, and `evals/compare_embeddings.py`
prints the comparison table the day a key exists. The pipeline is the
deliverable; the embedder is a plug.

**Q: How do you know retrieval is any good?**
We measure it: an 18-question golden set, recall@1 / recall@5 / MRR, four
configurations compared — dense 0.78/0.94/0.86, hybrid 0.72/1.00/0.86,
dense+rerank 0.89/1.00/0.94, and the default hybrid+rerank
**0.83/1.00/0.92**. It is a literal regression gate: CI runs
`evals/run_retrieval.py --memory --check` on every push and fails the build
if any metric drops below `evals/baseline.json`, because a chunking or fusion
change can keep every unit test green while making answers worse.

**Q: Your own table shows dense+rerank beating your default. Why is hybrid
the default?**
Good catch, and the honest answer is that the difference is one question out
of eighteen, in opposite directions: hybrid wins the lexical-gap question
("linter/formatter" against docs that say "lint/format", rank 3 → 2), dense
wins another. Both reach recall@5 = 1.00. At this corpus size that ordering
is noise, and the ablation cannot cleanly separate the two channels anyway —
`hash-512` is a *lexical* hash, so the "dense" arm is already keyword-ish.
Sparse stays on because it costs nothing at query time and is the insurance
against wording the embedder has never seen; the reranker is the stage that
measurably earns its place (+0.11 recall@1). With a real semantic embedder
the two channels genuinely diverge, which is what `compare_embeddings.py`
exists to measure.

Worth saying out loud: those baseline numbers were **wrong in the docs**
until an audit re-ran them. They had been measured before the relevance gate
landed, and only the headline was rechecked. That is why the eval is now a
build step and why `test_docs_consistency.py` asserts the numbers the
documents quote.

**Q: Where does the knowledge base come from?**
It starts **empty** — no seed data ships with the app. Documents are added
at runtime: a Documents panel in the UI, `POST /api/documents`, or an
ingest CLI, with an optional `ASSISTANT_CORPUS_DIR` for a folder you want
kept in sync. `evals/corpus/` exists only as the retrieval test fixture that
the golden set's answers live in.

**Q: What happens when retrieval finds nothing useful?**
It says so, and distinguishes two cases the model must handle differently:
*nothing indexed yet* (the user's problem — upload documents) versus
*nothing relevant* (the docs don't cover it — don't retry). This exists
because vector search **always** returns its top-k, even for a query about
something the corpus has never heard of. The relevance gate drops chunks
sharing no meaningful token with the query, so "empty" means the same thing
to every caller.

---

## Vector database

**Q: Why Qdrant over pgvector, Weaviate, or Chroma?**
Native **named vectors** — one point carries both a dense and a sparse
vector — plus server-side **RRF fusion** through the Query API, which is
exactly the hybrid search we wanted without hand-rolling fusion in Python.
It runs as one container locally and has a first-class async client. The
honest counter-argument: if you already run Postgres, pgvector avoids a new
datastore, and at this corpus size you would not feel the difference.

**Q: What are dense and sparse vectors actually doing?**
Dense captures *meaning* — "invoice" near "billing" even with no shared
words. Sparse is classic keyword matching, so exact tokens like a service
name or an error code score highly. They fail in opposite directions, which
is why fusing them is the safer default.

Be precise about what our own numbers show, because they do not show sparse
winning outright: adding sparse takes recall@**5** from 0.94 to **1.00**
(every question lands in the top 5) while recall@1 moves 0.78 → 0.72, and
after reranking 0.89 → 0.83 — one question either way on an 18-question
set. The honest reading is that at this corpus size, with a *lexical* hash
embedder standing in for the dense channel, the ablation cannot separate the
two signals; sparse is cheap insurance whose value shows up on unseen
wording, and the reranker is the stage that measurably earns its place.

**Q: What is RRF, in one sentence?**
Reciprocal Rank Fusion merges two ranked lists by scoring each document on
`1/(k + rank)` in each list and summing — so it combines *rankings* rather
than incomparable similarity scores.

**Q: How would this scale?**
Qdrant handles millions of vectors on one node; the current corpus is tens
of chunks. The parts that would need attention first are re-ingest strategy
(incremental rather than full), embedding cost at volume (batching is
already there), and the `list_sources` scroll in the documents API, which is
fine for hundreds of documents and would need a payload index beyond that.

---

## Agents, tools & MCP

**Q: What actually is the "agent" here?**
A ReAct loop: the model sees the conversation plus tool schemas, may emit
tool calls, the loop executes them and appends the results, and repeats —
bounded at 6 iterations — until it produces a final answer. Everything
streams to the browser as typed events.

**Q: The GitHub demo is mocked — so what did you actually prove?**
Everything except GitHub's data: subprocess spawn, MCP handshake, tool
discovery, namespacing, argument passing, result handling, error handling —
all real. The code-search MCP server is *fully* real and searches this
repository. The mock deliberately uses the official GitHub server's tool
names, so the swap is one config line plus a PAT, with no code changes.

**Q: Why MCP instead of writing integrations directly?**
N+M instead of N×M. GitHub's MCP server already exists and is maintained by
GitHub — we never write or maintain that integration. And our `code_search`
server is instantly reusable by any MCP client, not just this app.

**Q: Is running MCP servers safe?**
They're dependencies — the same trust model as a pip package: run trusted
ones, least privilege. Ours are local; `read_file` is jailed to the repo
root; secrets reach servers through environment variables, never through the
model's context; and an unreachable server is skipped with a warning rather
than taking the app down (`/api/health` reports `degraded` when an enabled
server failed to connect).

**Q: How do you stop a tool from taking the whole turn down?**
`Tool.run` is a single execution seam for every tool on every backend: a
crash becomes an `error:` *result* the model can react to, never an
exception that kills the loop. It's also where the span, metrics, structured
log and duplicate guard live — added once, not per tool.

---

## Observability

**Q: Why so much observability for a demo?**
Because "the model said something wrong" is unactionable without it. The
goal was that every answer can be explained: what was retrieved, which tools
ran, how long each step took, how many tokens, what it cost. That's a
product feature here, not just ops — the stats line and the "details"
timeline are in the UI.

**Q: What can you actually see?**
Five surfaces on the same data. **Logs** — structured, with
`session_id`/`turn_id`/`backend` auto-bound to every line, one greppable
`turn.summary` per turn. **Metrics** — `/metrics` exposes 9 `assistant_*`
metric families (turns, turn/LLM/tool/retrieval latency histograms, tokens,
cost, tool calls by status, errors by kind), labelled by
backend/provider/tool/mode.
**Traces** — OTel spans on the seams that explain the agent:
`agent.turn → llm.step → tool.execute → rag.retrieve`, in Jaeger. **Product**
— per-turn stats and an expandable timeline in the chat. **Audit** — the
last 50 turns per session, replayable via the API.

**Q: Why manual spans instead of auto-instrumentation?**
Auto-instrumentation shows you HTTP calls. It cannot tell you *why the agent
did that* — which tool it chose, what retrieval returned, how many
reasoning steps it took. The four manual spans are the ones that make a
trace explain the agent rather than the transport.

**Q: Does all this cost anything when it's off?**
No. Tracing is inert with no destination configured — no SDK import, no
network, a no-op tracer. Metrics are in-process counters. It's offline-first
by design: Jaeger, Prometheus and Grafana all run locally with zero
accounts.

---

## Engineering quality

**Q: How do you test something nondeterministic?**
Remove the nondeterminism from every layer except the one under evaluation:
scripted LLMs for the loop's branches, `FakeLLM` + fakeredis + in-memory
Qdrant for protocol tests, a deterministic embedder for retrieval evals.
**244 tests in ~22 seconds, fully offline** — no network, no containers, no
keys. Model *quality* is deliberately out of unit scope; that's what the
eval harness is for.

**Q: What does CI actually enforce?**
Ruff (lint + format), pyright, the test suite with a coverage floor, a
frontend job (typecheck + tests + build), a Docker image build, and the
retrieval quality gate — across Python 3.12 **and** 3.13, because 3.13 is
what the image ships and testing only the floor version was a real gap. A
separate security workflow runs CodeQL plus `pip-audit` and `npm audit`
weekly.

**Q: Did the security scanning find anything?**
Yes, immediately — which is the point. Eight Python vulnerabilities across
four packages and one critical frontend one: a `happy-dom` VM-context escape
(RCE), SSRF and path traversal in `pydantic-ai`, command injection in
`fastmcp`, pickle deserialisation in `diskcache`. All fixed by upgrading;
both audits now report zero. The upgrade meant absorbing four upstream
breaking changes, which is the honest cost of staying current.

**Q: What was the hardest bug?**
Two worth telling. A **silent correctness bug**: `retry-after: 0` means
"retry immediately", but `x or default` treats `0.0` as absent — so the
client slept 2s then 4s while claiming to honour the header, and it also
made 12 of the suite's 20 seconds real sleeping. And a **drift bug**: the
offline fake for the pydantic-ai backend was a hand-copied twin that never
learned about a new tool, so one backend silently behaved differently from
the other two — invisible precisely because the duplication looked harmless.
Both now have regression tests; the second produced a whole parity test file.

**Q: How do you know the documentation is true?**
Partly automated: `tests/test_docs_links.py` fails the build on any broken
relative link, on stray prose outside `docs/`, and if the index stops
covering a folder. It caught seven broken links the moment a module was
split into a package. The prose claims are kept honest by re-verifying
numbers before citing them — everything in the table at the top of this page
is reproducible with one command.

**Q: How do you stop a runaway answer, and what does it cost?**
A `cancel` frame. That is the bidirectional part of the WebSocket earning its
keep — with SSE the client would have to drop the connection. It needed one
structural change: the receive loop cannot read a frame while it is awaiting
the answer, so each turn runs as its own `asyncio.Task`. Cancelling it lands
inside `async for event in agent.run(...)`; leaving that loop closes the async
generator, which runs the `finally` blocks that end the spans and release the
provider stream — no separate cleanup path that could drift. A stopped turn
is not an error: the partial answer stays on screen and in history (marked
`[stopped by the user]`, so the next turn does not re-answer from scratch),
the tokens really were spent so the cost is still recorded, and the metric
`assistant_cancelled_turns_total` is deliberately excluded from the latency
histogram — a stopped turn measures the user's patience, not the system's.

**Q: What stops one client burning your whole quota?**
A sliding-window rate limiter in Redis: 20 chat turns per minute per session,
50 indexing writes per hour per caller, both configurable, both checked
*before* any LLM call. A sliding log rather than `INCR`+`EXPIRE`, because a
fixed window lets a burst across the boundary through at double the limit; in
Redis rather than memory, so it survives more than one worker. Be precise
about what it is: a budget guard against a stuck client, not access control.
Real per-user quotas need user identity, which needs OIDC — and then it is
the same limiter keyed on the subject instead of the session.

**Q: What would you do next, with more time?**
In order: a real-model eval pass plus the embedding comparison rows, OIDC
(which also upgrades the rate limits from per-session to per-user), long-term
memory as a facts store in Qdrant, and a Redis checkpointer to make LangGraph
runs durable — at which point its checkpointing becomes a genuine
differentiator rather than a demo.

---

## Questions to ask *back*

Defence goes better when it's a conversation:

- "Which part would you want to see running first — the RAG demo, the MCP
  tool call, or the trace waterfall?"
- "Is your team's constraint cost, latency, or data residency? Because the
  provider is a config value, and that choice changes the answer."
- "Would you want documents uploaded by users, or a synced folder? Both are
  supported and they have different threat models."
