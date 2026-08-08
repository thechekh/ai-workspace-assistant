# Changelog

Notable changes, newest first. The phase-by-phase build history with
acceptance evidence lives in
[docs/project/implementation-plan.md](docs/project/implementation-plan.md);
this file records what changed after the initial nine phases.

## Unreleased

### Added
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
- `retry-after: 0` was discarded as falsy, causing needless multi-second
  stalls on rate limits.
- `WebSocketDisconnect` was counted and logged as a server error.
- The pydantic-ai offline fake had drifted and never learned `fetch_url`.
- The Docker build was broken (`README.md` was never copied) and the Vite
  dev server did not proxy `/api`.
