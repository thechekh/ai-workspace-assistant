# Tools evaluated and deferred — the decision record

**Every tool idea that reached a real evaluation and did not ship: the cost
to build it, the cost to run it, and why the POC said no.** This is a
decision record, not a wishlist — for what shipped instead, see
[implementation-plan.md](implementation-plan.md). Verdicts current as of
2026-09-04.

Every tool idea that reached a real evaluation and did **not** ship, each with
its purpose, what it would cost to build and to run, why the POC says no, and
what would change the answer. This is deliberately a *decision record*, not a
wishlist: at the workshop, "we considered it and here is the reasoning" beats
both "we didn't think of it" and a feature nobody can defend.

Two principles filtered everything below. They are worth stating once, up
front, because they answer most "why not X?" questions before they are asked:

1. **The nano filter.** This POC runs `gpt-4.1-nano` ($0.10/1M prompt tokens).
   A small model does not synthesize well across large tool results — so
   *intelligence migrates from the agent into the tools*: results must arrive
   short, pre-formatted, and pre-digested. Any tool whose value depends on the
   model digesting a big structured dump fails this filter regardless of how
   buildable it is.
2. **The docstring is policy.** The description is the only interface the
   model reads, so one sentence there ("call this first whenever…") can
   silently repeal a consent rule pinned by a test. Stateful or cost-incurring
   tools get conservative docstrings; routing convenience never overrides the
   explicit-ask rule on the one write tool.

## 1. The scorecard

| Tool | One line | Build cost | Run cost | Verdict |
|---|---|---|---|---|
| `index_repo` (clone-based) | clone + symbol-chunk a repo locally | days + new infra class | low | **Superseded** by `ingest_repo(include_code)` |
| `repo_map` | one-call structural digest of a repo | ~1 day (lite: hours) | ~2–6k tokens/call | **Deferred** — fails the nano filter |
| `repo_insights` | git-history analytics (churn, bus factor) | days — *requires* clones | ~1–3k tokens/call | **Deferred** — sole justification for clone infra |
| Symbol-level `code_kb` | AST-chunked semantic code search | ~1 day | embedding spend per repo | **Deferred** — paragraph chunks + sparse lexical proved sufficient |
| `get_doc` | fetch a full document after a chunk hit | hours | up to ~10k tokens/call | **Rejected** — cost amplifier |
| `run_sql` | read-only SQL over an analytics DB | ~1 day done right | low | **Rejected** — no database exists |
| `jira_search` (Atlassian MCP) | Jira/Confluence via hosted Rovo server | ~zero code (config) | +tool schemas/prompt | **Rejected** by owner — no org to demo against |
| GitHub `repos` toolset | vendor code-read tools | zero code (one header) | +10–15 schemas every prompt | **Superseded** by native `repo_read_file` (tokenless) |
| `workspace-mcp` over HTTP | serve our tools to editors | ~hours + auth design | none | **Deferred** — mechanism already proven both directions |

## 2. The details

### `index_repo(url, ref)` — local clone as the enabler
**Purpose:** shallow-clone into a managed folder, symbol-chunk, index; the
foundation for `repo_map` and `repo_insights`.
**Why not:** the POC took the API-ingestion path instead —
`ingest_repo(include_code=true)` covers the same user story (paste a repo,
ask questions) with none of what cloning drags in: no `git` subprocess, no
disk-state management (size caps, LRU eviction), no clone-host allowlist, no
submodule attack surface. And it stays **tokenless for public repos**, which
was a hard requirement. Cloning is not wrong — it is a *platform* decision,
and this is a POC that had just shed a scheduler and an endpoint to stay one.
**Would change the answer:** needing full git history or >100-file repos —
i.e. exactly the two tools below becoming requirements.
**Salvaged from the idea:** the *orientation summary* (languages, file count,
top-level dirs, README first line, returned by ingest) — short, pre-digested,
zero new surface. It passes the nano filter; the rest of the pitch did not.
**Rejected outright:** its docstring advice, *"call this first whenever the
user provides a repository URL"* — that sentence would make the one write
tool fire on any pasted link, un-asked. Pasted links route to `fetch_url`
(cheap, read-only summary); ingestion happens on explicit request only, and a
test pins that.

### `repo_map(repo, focus_dir)` — the one-call structural digest
**Purpose:** replace thirty `get_file_contents` calls with one ranked digest:
tree, entry points, dependency manifests, top-level symbol signatures. The
lineage is Aider's repo-map, and the idea is genuinely good.
**Why not:** it fails the nano filter — its value assumes a model that
reasons well over a ~6k-char structured dump, and the model in this POC
needed to be told twice to paste a 15-line function. Secondary: no clone
needed for a lite version (the ingest step already holds the tree, manifests
and file contents), so if it ever ships it is an *extension of `ingest_repo`*,
not a cloning pipeline.
**Would change the answer:** a bigger model, or a user base asking "where do
I start in this repo?" often enough to justify tuning the digest.

### `repo_insights(repo, months)` — git-history analytics
**Purpose:** churn hotspots, bus factor, staleness, activity trend — "what is
risky to touch?", "is this project alive?". Structurally awkward for the
GitHub API (hundreds of paginated calls), natural over a local `git log`.
**Why not — the decisive argument:** it is the only tool that genuinely
*requires* clone infrastructure, and standing up clones for a single tool is
the tail wagging the dog. (The "our demo repo has two commits" objection is
real but weak on its own — the fix would be demoing against a large public
repo; the infrastructure bill is what kills it.)
**Would change the answer:** clone infrastructure existing anyway — at which
point this becomes the most attractive tool on this page.

### Symbol-level `code_kb` (AST chunking, dedicated collection)
**Purpose:** higher-precision semantic code retrieval — chunk by
function/class instead of by paragraph.
**Why not:** measured sufficiency. Paragraph-level chunks of ingested code
plus the hybrid index's **sparse lexical vector** (which matches identifiers
exactly) answered "show me the code that scores matches" on the first try
against a real repository. Symbol chunking is a quality upgrade with real
build cost, deferred until retrieval quality is actually the bottleneck.

### `get_doc(source)` — whole document after a chunk hit
**Purpose:** reduce hallucination on truncated excerpts by fetching the full
document.
**Why not:** it is a cost *amplifier* — up to ~10k tokens per call on a
budget-constrained POC — and `repo_read_file` now covers the strongest case
(opening the full *source file* behind a code chunk). Chunks with citations
have been sufficient for every live-tested question.

### `run_sql(query)` — read-only SQL
**Purpose:** analytics questions over an operational database.
**Why not:** there is no database with data in this project — the tool would
have nothing to query. The security story is also instructive enough to keep
as a slide: the archived reference Postgres MCP server shipped a known SQL
injection that bypassed its read-only mode, which is why the recipe (SELECT-
only role on a replica → read-only session + timeout → AST walk → forced
LIMIT) matters if this is ever built. See
[security.md](../reference/security.md).

### `jira_search` — Atlassian's hosted MCP server
**Purpose:** the brief's Jira integration.
**Why not:** owner decision — no Atlassian org to demo against. The honest
workshop line: since [`MCPServerConfig`](../../src/assistant/config.py) grew
auth `headers`, plugging the hosted Rovo server is **one config entry plus a
token** — the same one-line shape the GitHub server already proves live.
Nothing about the architecture is waiting on code.

### GitHub `repos` toolset — vendor code reads
**Purpose:** `get_file_contents` etc. over any repo the PAT reaches.
**Why not:** superseded by the native `repo_read_file`, which is tokenless
for public repositories (the vendor route requires a PAT for everything) and
adds one tool schema to the prompt instead of ~10–15 on every single turn.

### Claude via a Max subscription as the gateway's LLM — **not permitted**
**Purpose:** run the same chat pipeline with Claude (e.g. Sonnet) billed to
a consumer Claude subscription instead of the OpenAI key.
**Why not — Anthropic's own terms:** a subscription licenses Anthropic's
surfaces (claude.ai, Claude Desktop, Claude Code), not third-party backends.
The Agent SDK quickstart states it directly: *"Unless previously approved,
Anthropic does not allow third party developers to offer claude.ai login or
rate limits for their products, including agents built on the Claude Agent
SDK."* So every route — OpenAI-compatible bridge proxies over Claude Code
credentials, or the Agent SDK riding the subscription login — is excluded.
**What is permitted, and was verified:** the reverse direction — our tools
served *into* Claude Code/Desktop over MCP, where the subscription pays for
the model and this project only supplies tools. Prototyped on a branch
(streamable-HTTP server exposing `search_docs`, `repo_read_file`,
`ingest_repo`, `list_documents`; our own `MCPRegistry` consumed it; Claude
Code registered it), then **deleted as not needed** for the POC.
**The buildable version:** Claude *inside* this gateway is an **API-key**
decision — the official `anthropic` SDK as a fourth provider behind the
`InstrumentedLLM` seam, ~half a day; at the measured ~7k-token turns,
Sonnet ≈ $0.015 and Haiku ≈ $0.008 per turn versus $0.0008 on nano.

### `workspace-mcp` over streamable HTTP
**Purpose:** serve this project's own tools to the whole department's
editors (Cursor, Claude Code).
**Why not:** the mechanism is already proven in both directions — our servers
are MCP SDK servers (HTTP is a transport flag away) and our client consumes GitHub's
hosted streamable-HTTP server in production. What is missing is a *reason*:
the useful tool to serve would be `search_docs`, and exposing the knowledge
base over unauthenticated HTTP contradicts the security posture. "We can,
and chose not to yet" is the stronger demo.

## 3. The narrative this page supports

The [brief](description.md) describes the target platform; this POC is the
**deliberate subset**, and the cuts on this page — Jira, the
scheduler, local clones, the big model — are engineering judgment under
constraints, not gaps. Presenting it that way is the strongest material in
the workshop: every row above is a decision that can be defended, priced,
and reversed on a stated trigger.

## 4. Other deferred work — not tools, same discipline

The rest of the former backlog, kept here so "what's next?" has one answer.
Each is deliberate, and each says what would trigger it.

| Item | Status / why deferred | Trigger |
|---|---|---|
| **OIDC / SSO** at the gateway (replacing the single bearer token) | Single-tenant POC; no user identity to key on | Any second team, or per-user quotas/audit |
| **Long-term memory facts store** (distilled facts in Qdrant across sessions) | A wrongly extracted fact poisons every later conversation; needs provenance, TTLs, a correction path | A user base that asks the same things across sessions |
| **LangGraph Redis checkpointer** (durable, resumable runs) | The LangGraph backend is one of three equals; its flagship persistence is not needed for one-turn tools | Long multi-step runs that must survive a restart |
| **Cloud tracing backends** (Logfire / Langfuse) | **Done, 2026-09-04** — both verified live on real turns, across all three backends; see [../reference/logfire-langfuse.md](../reference/logfire-langfuse.md) | — |
| **Grafana is fully open** (anonymous admin) | Correct for localhost | The compose file seeding any shared deployment |
| **Dev-grade SSRF guard** (no DNS resolution) | Address *literals* are parsed with `ipaddress`, so loopback, RFC 1918, link-local, CGNAT, IPv4-mapped IPv6 and the decimal/hex spellings are all refused; what remains is that a public *name* resolving to a private address is not caught | Leaving localhost — resolve DNS, allowlist egress |
| **openai 3.8+, pydantic-ai 2.42, websockets 17** | Held back by one chain, not by us: `ragas` 0.4.3 (the LLM-judge eval) requires `openai<3.8`, pydantic-ai 2.42 requires `openai>=3.8`, and pydantic-ai caps `websockets<17` via google-genai. Splitting `evals` into its own lock would free all three, and was rejected twice: the judge runs the app's own code to produce the answers it scores, so a judge environment on a different SDK than production would be measuring a different stack — and the two mechanical routes tried on 2026-09-10 fail on their own terms (`[tool.uv] conflicts` does not fork because project dependencies apply to every fork; a `python_full_version` marker would make CI on 3.12/3.13 test a different openai than a developer on 3.14) | A ragas release that accepts openai 3.8+ (there is no 1.x yet), or the judge moving off ragas |
| **`pyright` strict** | **Done for `src/`, 2026-09-10** — `strict = ["src"]` at 0 errors, every one of the 52 fixed rather than suppressed (two annotated third-party exceptions: redis-py's bare `Callable` overloads, `langgraph.graph`'s missing `py.typed`). Tests and evals stay `standard` on purpose: a fake that ignores half a protocol signature is fine there, and the ~550 `reportUnknown*` hits a strict pass would raise are annotation noise, not defects | A typing pass over the test suite as its own task, if ever |
| **CodeQL upload** | **Done** — the job was gated on the repository being public, and it is: `security-extended` queries run for Python and JavaScript/TypeScript on every push and weekly, results uploaded to the Security tab | — |
| **One Python version** (venv 3.14, CI 3.12+3.13, Docker 3.13) | A `.python-version` file forced a venv rebuild that failed on locked files | Decide the target, then add the file deliberately |
| **TypeScript 7** | Two blockers, the nearer one measured on 2026-09-10: `typescript-eslint` 8.70 peer-caps TypeScript at `<6.1.0` and has no 9.x, so `npm install` refuses the pair outright; behind it, `vue-tsc` embeds the compiler and TS 7's native rewrite has no compiler API yet | typescript-eslint accepting TS 7, then vuejs/language-tools |
| **`LICENSE`, `CODEOWNERS`, issue/PR templates** | Owner's choice; `CONTRIBUTING.md` / `SECURITY.md` were added and removed as clutter for a single-maintainer POC | Publishing, or a second maintainer |
| **Workshop prep** | Mermaid sequence diagrams for the theory chapters; a mock Q&A rehearsal against [qanda](../qanda/README.md) | The week of the workshop |

## 5. Related

- [implementation-plan.md](implementation-plan.md) — what shipped instead, phase by phase
- [tech-stack.md](tech-stack.md) — the decisions that were made instead
- [description.md](description.md) — the brief this POC deliberately narrowed
- [../reference/security.md](../reference/security.md) — the SQL-injection story behind the `run_sql` verdict
- [../qanda/README.md](../qanda/README.md) — "why not X" questions answered in interview form
