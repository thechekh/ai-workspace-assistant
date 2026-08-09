# Changelog

Notable changes, newest first. The phase-by-phase build history with
acceptance evidence lives in
[docs/project/implementation-plan.md](docs/project/implementation-plan.md);
this file records what changed after the initial nine phases.

## Unreleased

### Added
- **Stop button** (and `Esc`) to interrupt an answer mid-stream. Turns run as
  their own task so the socket stays free to read a `{"type": "cancel"}`
  frame; the partial answer is kept, stored as history with a
  `[stopped by the user]` marker, and its real (partial) cost is still
  accounted for. Closing the tab cancels the turn the same way.
- **Rate limiting**: sliding windows in Redis, per session for chat turns and
  per caller for the indexing endpoints, refusing before any LLM call.
  Configurable via `ASSISTANT_RATE_LIMIT_*`; reads are never throttled.
- **Conversations panel**: list, reopen and delete recent chats
  (`GET /api/sessions`, `GET /api/sessions/{id}/messages`,
  `DELETE /api/sessions/{id}`). Sessions are indexed in a sorted set scored
  by activity, so listing never sweeps the keyspace, and reopening repaints
  the stored transcript rather than showing an empty window.
- **Retrieval quality gate in CI**: `run_retrieval.py --check` fails the build
  when golden-set metrics drop below `evals/baseline.json`, plus `--record`
  and `--trend` for the history in `evals/history.jsonl`. This is the one
  regression a unit test cannot catch.
- Metrics `assistant_cancelled_turns_total` and `assistant_rate_limited_total`.
- **Standard / Dev mode toggle** in the chat header. Standard is a plain
  conversation; Dev reveals tool cards and the per-turn stats line
  (duration, first-token latency, LLM steps, tokens, cost) with its
  expandable audit timeline. Purely presentational — every frame is still
  received, so switching is instant and retroactive.
- **Documents added at runtime**: `POST`/`GET`/`DELETE /api/documents` and a
  Documents panel in the UI. The knowledge base now starts empty; no seed
  data ships with the app.
- `fetch_url` tool for public web pages and GitHub repositories/accounts.
- Observability: structured logs with correlation IDs, Prometheus `/metrics`,
  OTel spans to Jaeger, deep health, a replayable audit trail, and per-turn
  cost accounting.
- Security scanning workflow (CodeQL, `pip-audit`, `npm audit`) and
  [docs/reference/security.md](docs/reference/security.md).
- ESLint + Prettier for the frontend, enforced in CI and pre-commit.
- Documentation tests: link integrity and cross-document fact consistency.

### Changed
- All dependencies upgraded to latest, absorbing four upstream breaking
  changes (pydantic-ai 2.x, `MCPServer` → `FastMCP`, MCP camelCase types,
  a third value from `streamable_http_client`).
- `docs_corpus/` → `evals/corpus/`: it is the retrieval test fixture, not
  product content.
- Structural refactors: `TurnRecorder` extracted from the turn handler,
  `build_runtime()` from the app factory, `agent/tools.py` split into a
  package, provider-error classification moved to `llm/errors.py`.

### Fixed
- **Stale retrieval ablation numbers in the docs.** The `0.56 / 0.67`
  dense/hybrid baselines were measured before the Phase 8 relevance gate and
  had drifted; re-measured, they are `0.78 / 0.72` (recall@1). The headline
  `0.83 / 1.00 / 0.92` was unchanged, which is exactly why nobody noticed.
  One claim was not merely stale but backwards — that adding sparse alone
  lifted recall@1 — and is now stated correctly: sparse buys recall@5, the
  reranker is the stage that earns its place. Every configuration in the
  table is now reproducible from the repository.
- Redis key name for the transcript in the sessions table
  (`session:{id}` → `session:{id}:messages`).
- `retry-after: 0` was discarded as falsy, causing needless multi-second
  stalls on rate limits.
- `WebSocketDisconnect` was counted and logged as a server error.
- The pydantic-ai offline fake had drifted and never learned `fetch_url`.
- The Docker build was broken (`README.md` was never copied) and the Vite
  dev server did not proxy `/api`.
