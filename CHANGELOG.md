# Changelog

Notable changes, newest first. The phase-by-phase build history with
acceptance evidence lives in
[docs/project/implementation-plan.md](docs/project/implementation-plan.md);
this file records what changed after the initial nine phases.

## Unreleased

### Fixed
- **A fabricated code answer, traced to four cooperating causes — each fixed
  at its own layer.** Asked "which file calculates the meter percentage?"
  about an ingested React repo, the assistant (a) once answered "not found in
  the indexed documentation" with **zero tool calls**, and (b) once invented
  a plausible file, variable and formula (`Meter.tsx`,
  `(progress / total) * 100`) — the real answer being `Progress.jsx` and
  `completedAmount / totalAmount`. The causes and fixes:
  1. **Tokenization** (the real retrieval bug): `completedPercentage` was
     lowercased before tokenizing, so the query word "percentage" could never
     match it and the relevance gate discarded the exact chunk holding the
     answer. A shared identifier-aware `tokenize()` (camelCase/ALLCAPS/digit
     splitting, whole identifier kept) now feeds the sparse encoder, the
     gate and the reranker alike. Golden-set metrics unchanged
     (0.83/1.00/0.92 — re-measured).
  2. **Our own prompt taught the surrender**: "do not repeat a similar call
     — say plainly that you could not find the answer" sanctioned one-try
     give-ups. Replaced with a retry contract (different terms, then report
     what was searched) plus two honesty rules: claims about repo/code
     content require a tool result from the current turn, and read-only
     tools are called, never asked permission for.
  3. **`search_docs`' zero-result text said "do not retry with a rephrased
     query"** — replaced by `_zero_result_help`: live per-repo inventory,
     indexed filenames sharing a query token, and the retry contract, at the
     exact point of decision.
  4. **`code__search_code`'s "no matches"** now states its scope (the
     assistant's own repo only) and redirects to `search_docs` for ingested
     repositories.
  Verified end to end on the original question: `search_docs →
  repo_read_file → Progress.jsx` with both real formulas, no permission
  question, no fabrication.

### Added
- **Code, without a PAT: `ingest_repo(include_code=true)` + `repo_read_file`.**
  The gap that made the project feel local-only is closed, and closed
  tokenless: for any *public* repository the agent can now index source files
  into the hybrid KB (the sparse lexical vector matches identifiers exactly;
  lockfiles, `node_modules` and minified bundles are skipped, ≤300 KB/file)
  and open any exact file on demand (`repo_read_file`, one validated GET —
  the model never supplies a URL). The flow is retrieval-then-read: ingest a
  repo with code → `search_docs` surfaces the chunk with its
  `owner/repo/path` source → `repo_read_file` shows the real file.
  `ASSISTANT_GITHUB_TOKEN` extends both to private repos; it is never
  required for public ones. Found by asking the live system "give me the
  block of code in charge of payment" — it reached for the only code tool it
  had, searched the wrong repository (its own checkout), and embellished the
  gap with invented providers.
- **Tool results capped at 20k chars before re-entering the prompt.** Tool
  output is billed prompt tokens; measured live, one PR listing against a
  busy repository came back as ~149k prompt tokens ($0.0154 — 57x a normal
  turn), and a large enough result would overflow the context. The cap sits
  in `Tool.run`, so native and MCP tools inherit it in all three backends,
  and the truncation marker tells the model to narrow the request rather
  than trust a silently partial listing.
- **`ingest_repo` — repository ingestion as an agent tool.** "Ingest the docs
  from owner/name" in chat makes the agent pull that repository's
  documentation (every `.md`/`.txt`/`.rst`, up to 100 files, ≤2 MB each) into
  the knowledge base: two listing requests plus one raw fetch per file,
  indexed with **`owner/repo/path` sources**. The namespace fixes a real
  incident — flat basenames let a second project's `README.md` silently
  replace the first's. Public repos need no credentials;
  `ASSISTANT_GITHUB_TOKEN` (fine-grained, read-only) unlocks private ones.

  This is the agent's **only write capability**, and it is additive-only: the
  read-only story becomes "read-only plus one named exception", pinned three
  ways — the allowlist test on the tool surface, `KB_WRITE_TOOLS` in the
  output guard (so a truthful "I updated the knowledge base" after a real
  ingest is not "corrected", while the same sentence without the tool call
  still is), and the system prompt naming the exception. A dedicated
  `POST /api/documents/from-repo` endpoint existed briefly and was folded
  into the tool: one capability, one place. Verified live end to end:
  ingest → cited answer → per-source delete. 13 offline tests (respx).

### Removed
- **The background-job layer, in full.** `worker.py` (taskiq broker +
  `reindex_docs` + the nightly `0 3 * * *` cron), the `taskiq`/`taskiq-redis`
  dependencies, the `worker` and `scheduler` compose services,
  `POST /api/reindex`, the `ASSISTANT_CORPUS_DIR` setting, and the UI's
  Re-index button.

  It was a no-op in every real configuration. Its only job was re-ingesting a
  folder of Markdown from disk, but the knowledge base is filled at upload
  time — a document is embedded once, when it arrives, so there was no batch
  left to schedule. With no corpus folder configured the nightly task logged
  "skipped" and returned 0, and the Re-index button returned 400 by design.

  What was lost is the "task queue" line in the stack description. The honest
  version is better: *documents are indexed on upload, so there is no batch to
  schedule.* Keeping a queue in order to be able to point at a queue is how
  dead weight gets defended. The taskiq-vs-arq decision record is kept in
  `tech-stack.md`, marked reversed — it was the right pick for a job that
  turned out not to exist.

  `POST /api/documents` bearer-auth coverage moved off the deleted
  `/api/reindex` and onto `/api/documents`, so the guard stayed tested.

### Changed
- **Groq removed; two modes, both documented.** The project now runs either
  fully **mocked** (`ASSISTANT_LLM_PROVIDER=fake` — no key, no network, no
  cost, and the entire tool loop still executes) or **real on OpenAI**
  (`gpt-4.1-nano` by default: the cheapest model that still calls tools
  reliably, about $0.0004 a turn). Switching is two lines in `.env`.
  `ollama` and `gemini` remain supported — same OpenAI-compatible shape, only
  the base URL differs — so the "provider is a config value" claim still holds.
- The provider hardening earned against llama models **stays**, reframed
  honestly: the `tool_use_failed` retry, `failed_generation` salvage and
  leaked-`<function>` parsing guard any OpenAI-compatible endpoint whose model
  emits malformed tool calls, with a local Ollama as the live case. Deleting
  tested robustness because one provider was retired would be a regression.
- `gpt-4.1-nano` priced in the cost table; without an entry it fell through to
  the unknown-model default and reported $0.00 a turn.

### Added
- **Groundedness measured with Ragas** (`evals/run_ragas.py`) — the LLM-judged
  faithfulness of an answer against the context it was given, which is
  hallucination expressed as a number. It closes the one gap the retrieval
  eval could never see: recall@k proves the right chunk was *found*, not that
  the model *used* it. Opt-in (`uv sync --group evals`), never in CI, and
  never a gate — every metric is an LLM call, the scores are
  non-deterministic, and ~35 extra packages would double the image.
- [docs/reference/metrics.md](docs/reference/metrics.md) — recall@k, MRR and
  groundedness in full: how each is computed, a worked example, what each one
  *hides*, and why retrieval is gated in CI while generation cannot be.
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
- Documentation tests: link integrity, cross-document fact consistency, and
  **coverage** — every setting, endpoint, metric, wire frame, tool, source
  file, dependency and run command must be mentioned somewhere, so shipping a
  feature without documenting it fails the build. It found four real gaps on
  the first run: the three `ASSISTANT_RATE_LIMIT_*` settings had reached
  `.env.example` but no handbook table, and `python-multipart` was an
  undocumented direct dependency.
- A field-by-field table for the per-turn stats line, including when
  `usage_estimated` is true and why a failed turn's cost reads low.
- **The glossary now covers every AI/ML concept the project uses** — 115
  entries across ten sections, up from 53. A sweep of the docs and the code
  for AI/ML terminology found 109 distinct concepts in use and only half of
  them on the one page you would revise from: *ablation, baseline, regression
  gate, relevance gate, top-k, corpus vs knowledge base, agentic loop, cost
  accounting, time to first token, correlation id, backpressure, untrusted
  model output* and thirty more were explained in a chapter somewhere but
  missing from the lookup.

### Changed
- **The embedder comparison is no longer hypothetical.** `text-embedding-3-small`
  measured against the offline `hash-512` on the same corpus and questions:
  recall@1 **0.83 → 0.94**, MRR **0.92 → 0.97**. Three questions the lexical
  hash ranked second or third move to first — the vocabulary-mismatch gap
  closing. It also reframes the hybrid-vs-dense ablation: with a *lexical*
  stand-in for the dense channel, dense and sparse were measuring nearly the
  same thing, which is why that comparison was so close.
- **Groundedness has a real number**: faithfulness **0.92** over four
  golden-set questions, judged by `gpt-4.1-nano`. Sanity-checked before being
  trusted — a supported answer scored 1.00 and a deliberately invented one
  ("every 5 minutes and written in Rust") scored 0.33, one claim of three.
- `gpt-4.1-nano` added to the price table; without it, cost accounting silently
  reported $0.00 per turn for the cheapest OpenAI model that still calls tools.
- **Coverage floor raised 82 → 84** (measured 84.7), and a **`pre-commit` CI
  job** with both toolchains — the ruff/eslint hooks duplicate other jobs on
  purpose, because what only this job can catch is a bug in the *hook wiring*
  (one previously made the eslint hook lint the wrong files), plus the
  yaml/toml/json, private-key, merge-marker and large-file checks that run
  nowhere else in CI.
- **`pytest-xdist`**, used in CI (`-n auto`) but deliberately not in `addopts`.
  Measured on the full suite: serial 26.3s, `-n 2` 19.0s, `-n 4` **16.1s**, `-n 8`
  22.4s, `-n auto` (12 workers) 30.1s — *slower than serial*, because each
  worker pays interpreter and fixture startup for a half-minute suite. CI uses
  `auto` because its runners have four cores.
- All dependencies upgraded to latest, absorbing four upstream breaking
  changes (pydantic-ai 2.x, `MCPServer` → `FastMCP`, MCP camelCase types,
  a third value from `streamable_http_client`).
- `docs_corpus/` → `evals/corpus/`: it is the retrieval test fixture, not
  product content.
- Structural refactors: `TurnRecorder` extracted from the turn handler,
  `build_runtime()` from the app factory, `agent/tools.py` split into a
  package, provider-error classification moved to `llm/errors.py`.

### Fixed
- **Re-uploading a document did not replace it.** Chunk ids are
  `uuid5(source, heading, index)`, so shortening a document or renaming a
  heading left the old chunks under ids nothing new collided with: deleted
  text stayed indexed, retrievable and citable. Ingest now deletes a source
  before re-adding it, which also fixes the nightly corpus re-index.
- **The pydantic-ai backend inherited none of the provider hardening.** It
  drives the model through pydantic-ai's own layer, bypassing `llm/client.py`,
  so it had no 429 backoff and no `tool_use_failed` retry — and failed live
  questions the other two backends answered. Retries re-implemented in the
  backend, sharing the policy helpers so the timing is defined once.
- **A failed turn never sent its `turn` frame.** The error path returned
  early, so clients waiting for the end-of-turn marker hung, no audit row was
  written, and the tokens already spent (three prompts, after retries) were
  missing from `assistant_cost_usd_total`. Every turn now ends with exactly
  one `turn` frame carrying `cancelled` / `failed`.
- **`GET /api/sessions?limit=0` returned every session.** `ZREVRANGE`'s end
  index is inclusive and negatives count from the end, so the cap inverted.
- **Leaked `(function=…` tool markup reached the user.** The salvage matched
  only `<function…`; llama-3.1-8b emits the paren form. Both are recovered,
  and prose that merely starts with those letters is no longer withheld.
- **A hallucinated tool name became a permanent Prometheus label.** Unknown
  tools now count under a fixed `<unregistered>` label; the name stays in logs.
- **`fetch_url`'s SSRF guard only checked the first URL.** With redirects
  followed, a public address could 302 to `169.254.169.254` and return the
  body. Every hop is now re-checked.
- Bearer tokens are compared with `secrets.compare_digest` (HTTP and WS), and
  the rate-limit key derives from a hash instead of a slice of the token.
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
