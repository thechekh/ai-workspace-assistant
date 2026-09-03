# Demo runbook — running the production stack

Development runs on fakes so the pipeline costs nothing to exercise. The demo
runs on the real thing. This is the checklist for the second one, and the
honest account of what "real" means for each component.

## The two profiles

| Component | Development ([`.env.example`](../../.env.example)) | Production ([`.env.production.example`](../../.env.production.example)) |
|---|---|---|
| LLM | `FakeLLM` — scripted, offline | OpenAI `gpt-4.1-nano` |
| Embeddings | `hash-512` — feature hashing | OpenAI `text-embedding-3-small`, 1536-dim |
| Vector DB | Qdrant `:memory:` | Qdrant container, on-disk |
| Sessions | `fakeredis://` | Redis container |
| MCP `code` | **real** (searches this repo) | **real** — unchanged |
| MCP `github` | mocked, canned PRs | GitHub's hosted MCP server |
| Traces / metrics | inert no-op tracer | Jaeger + Prometheus + Grafana |

The fakes exist for cost and CI, not because the real path is unfinished:
every row above is one environment variable. Nothing is stubbed in the
application code — [`build_embedder`](../../src/assistant/rag/embeddings.py)
and [`_default_mcp_servers`](../../src/assistant/config.py) pick an
implementation from config, and the agent cannot tell which it got.

## Setup

### 1. Credentials

Copy the profile and fill in two secrets:

```sh
cp .env.production.example .env
```

- `ASSISTANT_LLM_API_KEY` and `ASSISTANT_EMBEDDING_API_KEY` — the same OpenAI
  key works for both.
- A **GitHub PAT**, only if you want real PRs in the demo. Create at
  *github.com/settings/personal-access-tokens* → fine-grained, scoped to the
  repos you will show, **read-only**: Contents `Read`, Pull requests `Read`,
  Issues `Read`. Paste it into the `ASSISTANT_MCP_SERVERS` line.

Without the PAT everything else still runs real; only the `github` tools stay
mocked. That degradation is deliberate — see
[Graceful degradation](#graceful-degradation).

### 2. Infrastructure

```sh
docker compose up -d redis qdrant
docker compose --profile observability up -d      # jaeger, prometheus, grafana
```

### 3. The knowledge base — deliberately empty

Nothing pre-loads it, and that is part of the demo: the assistant starts
knowing nothing, says so honestly if asked, and learns whatever you feed it
*live on stage*. Fill it in front of the audience:

> **You:** ingest the docs from thechekh/demo-payments-platform
>
> **Assistant:** *(calls `ingest_repo`)* Indexed 28 chunks from 5 file(s)…

That is the whole flow — the ingestion is itself an agent tool, so the
audience watches the assistant *learn* on request. The UI's Documents panel
still handles ad-hoc file drops. Verified live: pointing it at a real
repository indexed its documentation in seconds and the very next question
was answered from it, with sources cited. Private repos need
`ASSISTANT_GITHUB_TOKEN` (the same read-only PAT the MCP server uses).

One embedding note: if the collection was ever built with a different
embedder, the width no longer matches and the next ingest recreates the
collection from scratch — previously indexed documents are gone, re-ingest
what you need.

### 4. Start and verify

```sh
uv run uvicorn assistant.main:app
curl -s localhost:8000/api/health | python -m json.tool
```

Every component must report real values before you present:

```json
{ "status": "ok",
  "components": {
    "redis":  { "status": "ok" },
    "qdrant": { "status": "ok", "collection": "docs", "points": 0 },
    "llm":    { "status": "ok", "provider": "openai", "model": "gpt-4.1-nano" },
    "mcp":    { "status": "ok", "servers_connected": "2/2" } } }
```

`"provider": "fake"` means the LLM step above did not take; `points`
climbs as you ingest.

## The three demo queries

These are the deliverables the brief asks for, in order. Measured against the
real stack:

| # | Ask | Query | Tool called | Cost |
|---|---|---|---|---|
| 1 | RAG demo | *"What is our deployment architecture?"* | `search_docs` | $0.000273 |
| 2 | MCP demo | *"Show the latest PRs in the repo"* | `github__list_pull_requests` | $0.000200 |
| 3 | Code search | *"Search the code for the rate limiter"* | `code__search_code` | $0.000190 |

Three turns cost **$0.00066** — about 1500 full demo runs per dollar. Watch
each one land in Jaeger (`localhost:16686`) as a four-span trace:
`agent.turn → llm.step → tool.execute → rag.retrieve`.

## Graceful degradation

Worth showing on purpose rather than hiding: kill the GitHub server mid-demo
and the assistant keeps working with the tools that remain.

```sh
docker stop bench_project-qdrant-1     # or point MCP at a dead URL
```

`/api/health` drops that component to `degraded`, the registry logs the
failure and continues, and the agent answers from what is left. That is
[`MCPRegistry.start`](../../src/assistant/mcp/registry.py) catching per-server
failures instead of aborting the whole registry.

## Cost control

The two levers that matter, both already set in the profile:

1. **Toolset scoping.** Every tool's JSON schema is re-sent in *every* prompt.
   The full GitHub server is ~44 tools (~12,900 tokens/prompt); scoped to
   `pull_requests,issues` it is a fraction of that. Unscoped, a demo question
   costs ~12x more for the same answer.
2. **Model choice.** `gpt-4.1-nano` at $0.10/$0.40 per 1M tokens. A tool-using
   turn is 2 LLM steps and lands around $0.0002.

Tests and CI never call a provider — they run on `fake` + `fakeredis://` +
in-memory Qdrant. Re-running the suite costs nothing.

## Teardown

```sh
docker compose --profile observability down
docker compose down
```

Sessions and vectors persist in named volumes (`redis-data`, `qdrant-data`),
so a restart keeps the index — no re-ingest needed unless the embedder changes.
