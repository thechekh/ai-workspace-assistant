# 12 — Defense Q&A

The questions most likely to come up, with answers that hold. General
technique: answer with a **decision + reason + evidence in the repo**, and
concede trade-offs proactively — the honest concession is what makes the
rest credible.

## Architecture

**Q: Why implement the agent three times? Isn't once enough?**
Because "which framework?" was a real open question, and we wanted an
evidence-based answer instead of a blog-post opinion. The marginal cost was
low (backends are 103/209/282 lines; everything else is shared), and the
payoff is `docs/backend-comparison.md` with measured numbers — plus the
choice stays reversible via config. That comparison *is* a deliverable of
the bench project.

**Q: Why a custom loop at all when frameworks exist?**
It's ~100 lines, it's the ground truth the frameworks wrap, and it makes
every debugging session easier because we know what "correct" looks like
underneath. It also became the teaching artifact for this workshop.

**Q: Why WebSockets instead of REST/SSE?**
Chat is bidirectional and session-shaped: one connection carries messages
up and tokens/tool-events down, with session resume on reconnect. SSE would
need a POST side-channel per message. (And the task spec names `ws://`.)

**Q: What breaks first in production?**
Honest list: (1) single shared auth token → replace with OIDC at the
gateway; (2) MemorySaver-style in-process state in the LangGraph backend →
Redis/Postgres checkpointer; (3) no rate limiting/quotas per user; (4) the
sessions sidebar and multi-user session management are future work. None of
these are architectural — the stateful parts (Redis, Qdrant) are already
externalized, so API pods scale horizontally today.

## LLM & cost

**Q: Which model does this run on, and what does it cost?**
The model is a config value behind an OpenAI-compatible layer. Everything
you saw today ran on a deterministic offline fake — **the entire project
was built and tested for $0 in API costs**. Flip one env var for Groq's
free tier (real Llama model), or a paid key for OpenAI. That cost story is
itself a design outcome: fakes for plumbing, real models only where quality
is being evaluated.

**Q: How do you prevent hallucinations?**
Grounding + transparency: the agent retrieves doc chunks and answers from
them, the tool card shows the user exactly what evidence was retrieved
(source file, heading, score), and the system prompt instructs "if the docs
don't cover it, say so". Retrieval quality is measured (recall@5 = 1.00 on
the golden set), so the right evidence reaching the model is a number, not
a hope.

**Q: What about prompt injection — a doc that says "ignore your instructions"?**
Real concern, honestly scoped: our corpus is trusted internal docs, and the
blast radius is bounded structurally — the model can only call allowlisted
read-only tools, execution is server-side, and tool arguments are
schema-validated. For untrusted corpora you'd add content sanitization and
tool-permission tiers; the registry is the natural enforcement point.

## RAG

**Q: Why RAG and not fine-tuning?**
Facts change weekly; re-ingest is minutes and free, retraining is neither.
RAG cites sources; fine-tuned knowledge is opaque and unauditable.
Fine-tuning is the tool for style/behavior, not for living documentation.

**Q: Your embedder isn't a real embedding model.**
Correct, and it's disclosed in the docs: `hash-512` is lexical feature
hashing — a deterministic, $0 stand-in that let us build and *measure* the
whole pipeline (0.83/1.00/0.92 on the golden set with hybrid+rerank).
Semantic models (OpenAI, voyage-3) are a config switch, and
`evals/compare_embeddings.py` prints the comparison table the day a key
exists. The pipeline is the deliverable; the embedder is a plug.

**Q: How do you know retrieval is good?**
We measure it: 18-question golden set, recall@1/recall@5/MRR, three
configurations compared (dense 0.56/0.94/0.72 → hybrid 0.67/1.00/0.80 →
+rerank 0.83/1.00/0.92). The eval runs self-contained in seconds
(`evals/run_retrieval.py --memory`) so it's a regression gate, not a
one-off benchmark.

## MCP

**Q: The GitHub demo is mocked — so what did you actually prove?**
Everything except GitHub's data: subprocess spawn, MCP handshake, tool
discovery, namespacing, call forwarding, error handling — all real (and the
code-search MCP server is fully real, searching this repo). The mock
deliberately uses the official GitHub server's tool names, so the swap to
real GitHub is one config line with a PAT — no code changes. That swap line
is on the slide.

**Q: Why MCP instead of writing integrations directly?**
N+M instead of N×M: the GitHub MCP server already exists and is maintained
by GitHub — we never write or maintain that integration. And our own
`code_search` server is instantly reusable by any MCP client (Claude
Desktop, editors), not just this app.

**Q: Is running MCP servers safe?**
They're dependencies — same trust model as a pip package: run trusted ones,
least privilege. Ours are local and ours; `read_file` is jailed to the repo
root (path-traversal test in the suite); secrets go to servers via
environment, never through the model's context; and unreachable servers
degrade gracefully instead of taking the app down.

## Engineering quality

**Q: How do you test something nondeterministic?**
Remove the nondeterminism from every layer except the one under evaluation:
scripted LLMs for the loop's branches, FakeLLM + fakeredis + in-memory
Qdrant for protocol tests, a deterministic embedder for retrieval evals.
72 tests, ~10 seconds, offline. Model *quality* is deliberately out of unit
scope — that's what the eval harness with real models is for.

**Q: The same tests pass on all three backends — why does that matter?**
It's the proof that the abstraction is real. The WS suite is parametrized
×3: identical assertions on custom, Pydantic AI, and LangGraph — including
memory-bounding behavior. If a backend cheated on the contract, the suite
would say so.

**Q: What was the hardest bug?**
A real infra outage during development: Redis died mid-message and silently
killed the WebSocket. The fix — every failure becomes a visible `error`
frame and the socket keeps serving — is now a test. Second place: framework
API drift (mcp 2.0 renames, LangGraph streaming needing the public astream
for token callbacks) — solved by probing installed APIs before writing
against them, which is why the adapters exist.

**Q: What would you do next with more time?**
In order: session management UI (list/switch conversations), real-model
eval pass with Groq/OpenAI + the embedding comparison rows, long-term
memory (facts store in Qdrant), OIDC, and a Redis checkpointer to make
LangGraph runs durable — at which point its checkpointing becomes a genuine
differentiator rather than a demo.
