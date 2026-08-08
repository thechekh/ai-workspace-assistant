# 09 — Testing, operations & troubleshooting

## The automated suite (212 tests, fully offline)

```sh
uv run pytest -q          # ~13s. No network, no Docker, no keys.
```

Determinism comes from swappable fakes: `FakeLLM` (scripted heuristics),
`fakeredis`, in-memory Qdrant, `HermeticSettings` (ignores your `.env`), and
scripted provider errors. Map of the suite:

| File | Proves |
|---|---|
| test_ws.py | WS protocol end-to-end **×3 backends**: streaming, history resume, tool loop, backend switch, bounded memory, zero-infra mode |
| test_observability.py | `turn` frame, /metrics series, deep health (ok + degraded), audit trail timeline, InstrumentedLLM usage capture |
| test_llm_errors.py | error mapping (429/401/404/5xx/network), 429 backoff, `stream_options` fallback, `tool_use_failed` retry + `failed_generation` salvage, leaked-tool-syntax parsing, cost table |
| test_fetch_url.py | fetch_url (mocked httpx: GitHub repo/account/HTML/SSRF guard), relevance gate, duplicate-call guard, FakeLLM URL routing |
| test_agent.py / test_tool_loop.py | the custom loop's mechanics |
| test_pydantic_backend.py / test_langgraph_backend.py | runtime parity |
| test_rag.py | chunking, hybrid search, rerank, ingest idempotency |
| test_mcp.py | real stdio MCP servers spawn + tools round-trip |
| test_memory.py | rolling summarization math |
| test_api_routes.py / test_config.py | REST + auth + settings |
| test_documents_api.py | documents added at runtime: upload (file + pasted), list, re-upload replaces rather than duplicates, delete, rejected types, auth, and the empty-knowledge-base message |
| test_fake_parity.py | all three backends route the same prompt to the same tool — the regression guard for the drift that made one backend's offline fake miss a tool |
| test_docs_links.py | the documentation itself: every relative link resolves, no stray prose outside `docs/`, the index covers every folder |

Quality gates (CI, every push): `ruff check` · `ruff format --check` ·
`pyright` (0 errors) · `pytest` with a coverage floor — on Python 3.12 **and**
3.13 — plus a frontend job (typecheck, vitest, build) and a Docker image
build. A second workflow runs CodeQL, `pip-audit` and `npm audit` weekly.

**Manual testing** is scripted feature-by-feature in
[the testing checklist](../reference/testing.md) (tiers: zero-infra → Docker → real
model → observability).

## Operating it

| Task | Command |
|---|---|
| API server (dev) | `uv run uvicorn assistant.main:app --reload` |
| Job worker (re-index queue) | `uv run taskiq worker assistant.worker:broker` |
| Nightly scheduler (cron 03:00) | `uv run taskiq scheduler assistant.worker:scheduler` |
| Re-index now (CLI) | `uv run python -m assistant.rag.ingest <folder> [--recreate]` |
| Retrieval quality | `uv run python evals/run_retrieval.py --memory` |
| Everything in containers | `docker compose --profile app up --build` |

**Auth mode**: set `ASSISTANT_AUTH_TOKEN=<secret>` → `POST`/`DELETE
/api/documents`, both `/api/sessions/{id}/turns[...]` routes and
`/api/reindex` need `Authorization: Bearer`, and the WS needs `?token=`.
Deliberately open: `GET /api/documents` (the panel lists before you
authenticate), open the UI once as `/?token=<secret>` (persisted). `/api/info`,
`/api/health`, `/healthz`, `/metrics` stay open by design. Production path:
replace with OIDC at a gateway.

**Secrets hygiene**: `.env` is gitignored; keys never live in code; `SecretStr`
keeps them out of logs. The `log_prompts` toggle is dev-only by policy
(conversations end up in logs).

## Troubleshooting (the failures you'll actually see)

| Symptom | Cause | Fix |
|---|---|---|
| Health dot **amber**, hover says `qdrant: error`; docs answers apologize | Qdrant container down / collection missing | `docker compose up -d`; re-ingest if the collection is empty |
| Chat: *"LLM rate limit hit (429). Provider says: … tokens per day (TPD): Limit 100000 …"* | Groq **daily** budget for the model is spent (a long session can do it) | switch `ASSISTANT_LLM_MODEL=llama-3.1-8b-instant` (own budget) or wait for reset / paid tier |
| Brief stalls mid-turn, logs show `LLM rate limited (429) — retry …` | per-minute limit; automatic backoff riding it out | nothing — working as designed |
| Chat: *"model failed to generate a valid tool call"* (rare) | llama emitted malformed tool JSON 3× and `failed_generation` was unparseable | resend / rephrase; already auto-retried + salvage-attempted |
| Answer contains raw `<function…>` text | should **never** happen now (salvage layer) — if seen, it's a new llama syntax variant | add it to `_LEAKED_CALL_PREFIX` in [llm/client.py](../../src/assistant/llm/client.py) + a test |
| *"duplicate call — … use the result you already received"* in a tool card | model repeated an identical call; guard answered | cosmetic; the model continues with the earlier result |
| *"No relevant documents found in the knowledge base …"* | relevance gate: the query shares no meaningful token with any chunk | expected for off-topic questions — the honest answer |
| `search_docs` returns chunks from a repo you tested with | you ingested extra sources into `docs` | `uv run python -m assistant.rag.ingest evals/corpus --recreate` |
| Chat: *"LLM authentication failed"* / *"Model not available"* | bad key / model name typo | fix `ASSISTANT_LLM_API_KEY` / `ASSISTANT_LLM_MODEL`, restart |
| UI loads but "disconnected" | server down, or auth on and no `?token=` | start server / open `/?token=<secret>` |
| Re-index button says "queued" but nothing happens | real Redis without a running worker | start the worker, or use the CLI ingest |
| No traces in Jaeger | `ASSISTANT_OTLP_ENDPOINT` unset, or stack not up | set it + restart server; check :16686 is reachable |
| Prometheus target down at `api:8000` | you run the server on the host | expected — `host.docker.internal:8000` is the live target |
| `fetch_url` shows GitHub page chrome instead of clean README | unauthenticated GitHub API rate limit (60 req/h) → HTML fallback | wait an hour, or accept — the model still reads it |
| JSON logs show `"exc_info": true` but no traceback | you're on a build older than the `format_exc_info` fix | pull latest |

## Extending safely (the pattern)

Any new capability should land with: a config default that works offline →
telemetry at the seam (span + metric + log) → a deterministic test with a
fake → a line in the docs. That pattern is why the platform stays green: the
suite catches contract breaks, and live quirks get reproduced as scripted
fakes the same day they're discovered (see test_llm_errors.py's history).
