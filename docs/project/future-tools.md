# Tools evaluated and deferred — the decision record

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

## The scorecard

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

## The details

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

### `workspace-mcp` over streamable HTTP
**Purpose:** serve this project's own tools to the whole department's
editors (Cursor, Claude Code).
**Why not:** the mechanism is already proven in both directions — our servers
are FastMCP (HTTP is a transport flag away) and our client consumes GitHub's
hosted streamable-HTTP server in production. What is missing is a *reason*:
the useful tool to serve would be `search_docs`, and exposing the knowledge
base over unauthenticated HTTP contradicts the security posture. "We can,
and chose not to yet" is the stronger demo.

## The narrative this page supports

The [brief](description.md) describes the target platform; this POC is the
**deliberate subset**, and the cuts on this page — Jira, the
scheduler, local clones, the big model — are engineering judgment under
constraints, not gaps. Presenting it that way is the strongest material in
the workshop: every row above is a decision that can be defended, priced,
and reversed on a stated trigger.
