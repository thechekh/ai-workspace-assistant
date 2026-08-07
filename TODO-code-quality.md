# Code-quality backlog — review findings (2026-08-07)

Findings from a full-codebase review: two parallel review agents (backend
code quality; infra/CI/frontend/tests) plus direct verification. This is
**separate from [TODO.md](TODO.md)**, which tracks features/roadmap — this
file is purely "make the existing code better".

**Status legend:** ✅ verified by running/reading the code myself ·
🔍 agent-reported, not independently re-verified (still cited to file:line).

Nothing here is broken enough to stop using the platform. P0 items are real
bugs with user-visible or metric-visible effects; everything below that is
about not letting the *next* bug through.

---

## P0 — Real bugs (fix first: ~half a day, each is independently testable)

- [ ] **`retry-after: 0` is silently discarded, causing needless multi-second stalls** ✅
  - **Where:** [src/assistant/llm/client.py:325](src/assistant/llm/client.py#L325)
    — `min(_retry_after_seconds(exc) or 2.0 * rate_limit_retries, _MAX_RETRY_DELAY_S)`
  - **Problem:** a provider replying `retry-after: 0` ("retry now") yields
    `0.0`, which is **falsy**, so the header is thrown away and we sleep 2s,
    then 4s. Verified: `0.0 or 2.0*1` → `2.0`.
  - **Also explains:** 12.5s of the ~20s test suite is these two tests
    sleeping for real (`test_create_stream_retries_rate_limits_with_retry_after`
    6.5s, `test_create_stream_gives_up_after_retry_budget` 6.0s).
  - **Fix:** `retry_after if retry_after is not None else 2.0 * rate_limit_retries`.
    Then monkeypatch `asyncio.sleep` in both tests so they *assert* the backoff
    schedule instead of waiting it out (suite drops to ~7s).

- [ ] **Closing a browser tab is recorded and logged as a server error** ✅
  - **Where:** [src/assistant/api/ws.py:233](src/assistant/api/ws.py#L233)
    (`except Exception as exc:` in `_handle_turn`)
  - **Problem:** `WebSocketDisconnect` subclasses `Exception` (verified) and is
    raised by `websocket.send_text(...)` when the client is gone. A user
    closing the tab mid-answer therefore increments
    `assistant_errors_total{kind="turn_exception"}`, emits a full
    `turn.failed` traceback, and then attempts *another* send on the dead
    socket. The metric you'd alert on is polluted by routine disconnects.
  - **Fix:** add `except WebSocketDisconnect: raise` **before** the broad
    clause (it's already handled by the caller at `chat_endpoint`). Consider
    tagging genuine internal bugs distinctly from provider errors.
  - **Test:** disconnect mid-stream, assert `errors_total` did not move.

- [ ] **FakeLLM demo heuristics are duplicated and have already drifted** ✅
  - **Where:** [src/assistant/llm/client.py:355-411](src/assistant/llm/client.py#L355)
    vs [src/assistant/agent/backends/pydantic_ai.py:149-194](src/assistant/agent/backends/pydantic_ai.py#L149)
  - **Problem:** two hand-maintained copies of the same keyword routing.
    Verified: `client.py` mentions `fetch_url` twice, the pydantic-ai copy
    **zero** times — so offline, `pydantic_ai` behaves differently from
    `custom`/`langgraph`, which undermines the backend comparison that is a
    centerpiece of this project. (Regression introduced when `fetch_url` was
    added; the duplication made it invisible.)
  - **Fix:** extract `decide_fake_action(user_text, tool_names) -> FakeAction | None`
    into e.g. `assistant/llm/fake.py`; both fakes render its result into their
    own event shapes.
  - **Test:** parametrize one "offline tool routing" test across all three
    backends — that's what would have caught it.

---

## P1a — Robustness gaps (~half a day)

- [ ] **No timeout on the LLM client → inherits a 600-second read timeout** ✅
  - **Where:** [src/assistant/llm/client.py:205](src/assistant/llm/client.py#L205)
    — `AsyncOpenAI(api_key=..., base_url=...)`
  - Verified SDK default: `Timeout(connect=5.0, read=600, write=600, pool=600)`,
    `max_retries=2`. A stalled provider pins a WebSocket turn for ten minutes
    with no frame to the user.
  - **Fix:** pass `timeout=httpx.Timeout(60.0, connect=5.0)` and
    `max_retries=0` (we hand-roll retries at `:305-333`, so SDK retries stack
    multiplicatively on top of ours).

- [ ] **Telemetry is lost when a turn is abandoned** 🔍
  - **Where:** [src/assistant/telemetry.py:117-145](src/assistant/telemetry.py#L117)
  - **Problem:** `LLM_STEP_SECONDS`, `TOKENS_TOTAL` and all `TurnStats` updates
    run *after* the `async for`. A `GeneratorExit`/`CancelledError` at the
    `yield event` skips every one of them — abandoned turns silently vanish
    from metrics and cost accounting.
  - **Fix:** wrap the post-loop block in `try/finally`.

- [ ] **`/api/health` reports MCP `ok` when zero servers connected** 🔍
  - **Where:** [src/assistant/api/routes.py:88-92](src/assistant/api/routes.py#L88);
    registry skips unreachable servers at
    [mcp/registry.py:43-49](src/assistant/mcp/registry.py#L43)
  - **Problem:** `start()` returns `[]`, `main.py` still assigns a non-`None`
    registry, so health returns `{"status":"ok","tools":[]}` and never goes
    `degraded` — in exactly the case where every MCP tool is gone. The deep
    health check exists to answer "can a turn actually succeed?".
  - **Fix:** record expected vs connected server counts on `app.state`; report
    `degraded` when connected < enabled.

- [ ] **No upper bound on user input** ✅
  - **Where:** [src/assistant/api/schemas.py](src/assistant/api/schemas.py) —
    `content: str = Field(min_length=1)`, no `max_length`
  - One pasted document goes straight into the prompt: your daily token budget
    in a single message. No rate limiting either.
  - **Fix:** `Field(min_length=1, max_length=8000)` (+ a friendly error frame).

- [ ] **`httpx` is imported by production code but declared dev-only** ✅
  - **Where:** imported at [agent/tools.py:16](src/assistant/agent/tools.py#L16)
    and [rag/embeddings.py:16](src/assistant/rag/embeddings.py#L16); declared
    under `[dependency-groups] dev` in [pyproject.toml:32](pyproject.toml#L32)
  - Resolves transitively via `openai` today — a latent break the moment that
    changes. **Fix:** move to `[project] dependencies`.

## P1b — The build can ship broken artifacts (~2 hours, highest leverage)

- [ ] **No `.dockerignore`** ✅ (verified missing)
  - Build context is **~427 MB / 30k+ files** (`.venv` alone is 354 MB) and
    **includes `.env`** — harmless today, a credential leak the moment anyone
    writes `COPY . .`.
  - **Fix:** add `.dockerignore`: `.venv/`, `**/node_modules/`,
    `frontend/dist/`, `.git/`, `.env*`, `*.png`, `.pytest_cache/`,
    `.ruff_cache/`, `.playwright-mcp/`.

- [ ] **Dockerfile copies host `node_modules` over the container's** 🔍
  - **Where:** [Dockerfile:10](Dockerfile#L10) — `COPY frontend/ ./` runs right
    after `npm ci` (line 9), overwriting freshly installed **Linux** deps with
    your **Windows** ones (esbuild/rollup native binaries), and defeating the
    dep-caching layer split entirely.
  - **Fix:** `.dockerignore` above solves it; also copy only what's needed.

- [ ] **CI never touches the frontend** 🔍
  - `.github/workflows/ci.yml` is one job with four Python steps. `npm run
    typecheck` and `npm run build` (`vue-tsc --noEmit && vite build`) never run,
    and the Docker image is never built. A TypeScript error merges green.
  - **Fix:** second job — `actions/setup-node@v4` with `cache: npm`, `npm ci`,
    `npm run typecheck`, `npm run build`.

- [ ] **CI tests Python 3.12; the shipped image runs 3.13** 🔍
  - [ci.yml:15](.github/workflows/ci.yml#L15) pins 3.12;
    [Dockerfile:14](Dockerfile#L14) is `python3.13-bookworm-slim`. The version
    that actually runs in production is never tested.
  - **Fix:** `strategy.matrix.python-version: ["3.12","3.13"]` + a
    `.python-version` file as the single source of truth.

- [ ] **CI uses `uv sync`, not `uv sync --frozen`** 🔍
  - If `pyproject.toml` drifts from `uv.lock`, CI silently re-resolves and
    tests versions the lock doesn't pin. (The Dockerfile already gets this
    right.) **Fix:** add `--frozen`.

- [ ] **CI missing concurrency, caching, timeout, permissions** 🔍
  - No `concurrency:` group (every push to an open PR runs a full duplicate,
    nothing cancels); `setup-uv` without `enable-cache: true` (full dep
    re-download each run); no `timeout-minutes` (a hung test holds a runner for
    6h); no `permissions:` block. ~8 lines total to fix all four.

---

## P2a — Testing (~1 day)

- [ ] **No coverage measurement anywhere** ✅
  - No `pytest-cov` installed, no `[tool.coverage]`, no `--cov` in CI — though
    `.gitignore` already ignores `.coverage`/`htmlcov/`, so the intent existed.
    129 tests and no idea what they don't touch.
  - **Fix:** add `pytest-cov`, `[tool.coverage.run] source=["src/assistant"]`
    + `branch=true`, then `--cov-fail-under=<today's measured number>` so it
    can only go up.

- [ ] **Zero frontend tests** 🔍
  - No vitest, no vue-test-utils, no Playwright. Untested: 6 components, the
    Pinia WS-event store, and **`frontend/src/lib/markdown.ts`, which renders
    LLM-generated markdown** — a sanitization regression there is an XSS path
    and is currently invisible.
  - **Fix:** add `vitest` + `@vue/test-utils`. Start with `markdown.ts`
    (hostile input) and `stores/chat.ts` (the token/tool_call/final reducer).

- [ ] **Five test modules import helpers from other test modules** 🔍
  - `test_langgraph_backend.py:20` and `test_memory.py:10` ← `test_tool_loop`;
    `test_llm_errors.py:25` and `test_observability.py:15-16` ←
    `test_ws`/`test_api_routes`.
  - Renaming a helper breaks unrelated suites, and importing a `test_*` module
    executes it at collection. The right pattern already exists for
    `HermeticSettings`/`build_seeded_retriever`.
  - **Fix:** move `ScriptedLLM`, `make_registry`, `collect_until_final`,
    `make_client` into `conftest.py` (or `tests/helpers.py`). Ruff's `TID`
    ruleset prevents recurrence.

- [ ] **pytest config is thin** 🔍
  - [pyproject.toml:65-67](pyproject.toml#L65) has 2 keys: no
    `--strict-markers`/`--strict-config`, no `filterwarnings = ["error"]`
    (deprecations from fastapi/pydantic/langgraph accumulate invisibly), no
    `asyncio_default_fixture_loop_scope` (pytest-asyncio ≥0.24 warns), and no
    `slow` marker for `test_mcp.py`'s real subprocess spawns (~4.5s).
  - Also: `pytest-xdist` isn't installed, though the suite looks xdist-ready
    (no shared files, per-test in-memory stores, `tmp_path` used correctly).

## P2b — Structure & duplication (~1–2 days, do after tests protect you)

- [ ] **`_handle_turn` is 154 lines with 8 positional parameters** ✅
  - [src/assistant/api/ws.py:151](src/assistant/api/ws.py#L151). Owns turn-id,
    contextvars, span attrs, memory fetch, a 5-branch dispatch that
    simultaneously forwards frames *and* builds audit dicts *and* counts
    tokens, error classification, cost math, summary assembly, logging, and
    Redis persistence.
  - **Fix:** extract a `TurnRecorder` (holds `audit`/`tool_names`/
    `first_token_ms`/`answer_chars`, has `observe(event)` + `summary()`) and
    `_persist_turn(...)`; pass a `TurnContext` dataclass instead of 8 args.

- [ ] **`stream_step` is 96 lines with two interleaved retry mechanisms** ✅
  - [src/assistant/llm/client.py:207](src/assistant/llm/client.py#L207).
    Six mutable flags across a `while True` → `async for` → `except APIError`
    that itself contains a recovery path. `pending`/`usage`/`held` are
    initialized twice (dead at `:222-224`, re-set at `:228-230`).
  - **Fix:** extract `_LeakedTextBuffer` (the hold-back heuristic) and
    `_ToolCallAccumulator`; leave `stream_step` as the retry shell. Use
    `async with await self._create_stream(...) as stream:` so the HTTP
    response closes deterministically instead of at asyncgen GC.

- [ ] **`create_app` is 109 lines with a 68-line nested `lifespan`** ✅
  - [src/assistant/main.py:42](src/assistant/main.py#L42). Test-injection
    params gate production wiring: passing `agent=` disables *both* Qdrant and
    MCP.
  - **Fix:** a `build_runtime(settings) -> Runtime` that tests override
    wholesale, instead of `if agent is None and ...` conditions.

- [ ] **Duplicated constants and builders across the three backends** 🔍
  - `_EVENT_RESULT_LIMIT = 1500` + its truncation expression in
    [custom.py:25](src/assistant/agent/backends/custom.py#L25),
    [langgraph.py:56](src/assistant/agent/backends/langgraph.py#L56),
    [pydantic_ai.py:52](src/assistant/agent/backends/pydantic_ai.py#L52)
    (verified: three copies) — three backends can silently diverge on how much
    tool output the UI sees, same bug class as P0-3.
  - `_to_openai_tools` duplicated inline in `langgraph.py:171-181`.
  - Provider/credential resolution duplicated between
    `client.py:414-426` and `pydantic_ai.py:197-215` (whose docstring literally
    says "mirroring `build_llm` exactly"); `pydantic_ai.py:50` imports the
    private `_stream_words` across a package boundary.
  - Dead `if self._tools is None` branches in `custom.py:87-88` and
    `langgraph.py:202-203` — unreachable since `fetch_url` is unconditionally
    appended in `main.py:85`.
  - **Fix:** `truncate_for_event()` in `agent/base.py`; reuse
    `_to_openai_tools`; one `resolve_provider(settings)` helper; make
    `ToolRegistry` non-optional and delete the dead branches.

- [ ] **HTTP clients created per call; SDK clients never closed** 🔍
  - `make_fetch_url` builds a fresh `httpx.AsyncClient` per invocation
    ([tools.py:236-240](src/assistant/agent/tools.py#L236)) — every call pays a
    fresh TCP+TLS handshake, and the GitHub path pays it for two requests.
    Same in `VoyageEmbedder.embed`. Conversely `OpenAICompatibleLLM._client`
    and `OpenAIEmbedder._client` are never `aclose()`d (the lifespan closes
    Redis/MCP/Qdrant but not these).
  - **Fix:** one pooled client created in the lifespan and injected;
    `await ...aclose()` in the lifespan `finally`.

- [ ] **`dict[str, object]` on real wire boundaries** 🔍
  - Audit records round-trip untyped through `SessionStore.append_turn` and out
    of `GET /api/sessions/{id}/turns`; `/api/info` and `/api/health` return
    bare dicts with no `response_model`. The audit schema is a real contract
    the frontend consumes, but nothing validates or documents it.
  - **Fix:** `TurnAuditEvent`/`TurnRecord` pydantic models beside `TurnSummary`;
    add `response_model=` to the three routes.

- [ ] **Module organization** 🔍
  - `_describe_llm_error` (50 lines of *provider* error classification) lives
    in the WebSocket transport at [ws.py:41-90](src/assistant/api/ws.py#L41) and
    has to import `is_tool_use_failure` from the LLM package to work → move to
    `assistant/llm/errors.py` (also fixes tests importing a private symbol).
  - The RAG relevance gate runs inside the tool handler
    ([tools.py:127](src/assistant/agent/tools.py#L127)) — the retriever
    shouldn't hand out chunks it knows are irrelevant → move into
    `Retriever.search`.
  - `agent/tools.py` (277 lines) is a grab-bag: registry + telemetry seam +
    GitHub REST client + HTML stripper → split `fetch_url` and helpers into
    `agent/tools/fetch.py`.

- [ ] **Blocking CPU/IO on the event loop** 🔍
  - `HashEmbedder.embed` is `async` but never awaits — a pure-Python loop over
    every chunk × token with an `md5` each; `load_chunks` does sync
    `read_text` per file; `POST /api/reindex` awaits `ingest(...)` inline in
    fakeredis mode — all on the loop serving live WebSocket chats. Latent
    today (corpus is 24 KB); stalls every chat as the corpus grows.
  - **Fix:** `asyncio.to_thread(...)` around embed + chunk loading.

- [ ] **Details panel over-fetches: 50 turns to render 1** ✅
  - [frontend/src/stores/chat.ts:214](frontend/src/stores/chat.ts#L214)
    downloads the whole audit list and filters client-side.
  - **Fix:** `GET /api/sessions/{id}/turns/{turn_id}` (or a `?turn_id=` filter).

---

## P3 — Hardening & repo hygiene (pick off opportunistically)

- [ ] **Container hardening** 🔍 — no `USER` (runs as **root**); no
  `HEALTHCHECK`; **no `healthcheck:` on any compose service** despite
  `/healthz` and `/api/health` existing, so `depends_on` waits for container
  start, not readiness; unpinned `:latest` on qdrant/jaeger/prometheus/grafana;
  no resource limits.
- [ ] **Compose can never reach a real LLM** 🔍 — no `env_file: [.env]`, no
  `ASSISTANT_LLM_API_KEY` passthrough, so `--profile app up` silently runs the
  **fake** provider regardless of your `.env`. (Grafana is also fully open by
  design — fine locally, a landmine if that file becomes a deploy starting point.)
- [ ] **No dependency updates or security scanning of any kind** 🔍 — no
  dependabot/renovate, no CodeQL, no `pip-audit`, no `npm audit`, no container
  or secret scanning; `.github/` contains exactly one file. The dep surface is
  large and fast-moving and the app makes **outbound fetches**. Highest-value
  P3 item.
- [ ] **pre-commit gaps** 🔍 — no `check-yaml` (6 YAML files incl. Grafana/
  Prometheus provisioning), `detect-private-key`, `end-of-file-fixer`, or
  `check-added-large-files` (5 PNGs were committed in `52e655a`, removed in
  `0eb31a6`, and persist in history); nothing formats TS/Vue (no
  prettier/eslint); CI never runs `pre-commit run --all-files`, so a
  contributor who skips `pre-commit install` is only caught for Python.
- [ ] **Linter/type strictness** 🔍 — pyright is `standard`, not `strict`
  (nearly free on an already-clean codebase); ruff `select` omits `S` (bandit —
  relevant given `fetch_url`/subprocess/MCP), `PT`, `T20`, `LOG`/`G`
  (structlog), and `TID`.
- [ ] **Missing repo-standard files** 🔍 — `LICENSE` (and no
  `license`/`authors`/`classifiers` in `[project]`), `CONTRIBUTING.md`,
  `CHANGELOG.md`, `CODEOWNERS`, `SECURITY.md`, issue/PR templates,
  `.python-version`, `.nvmrc`/`engines`, and no `CLAUDE.md`/`AGENTS.md`
  despite heavy agent-assisted development.
- [ ] **Known-and-documented, revisit if this ever leaves localhost:** the
  `fetch_url` SSRF guard is dev-grade (string match on host, no DNS
  resolution); no API rate limiting; unauthenticated GitHub API (60 req/h,
  falls back to HTML — consider an optional `ASSISTANT_GITHUB_TOKEN`).

---

## Suggested session plan

1. **Session 1 (~half day):** P0 1–3. Each gets a regression test — the
   parametrized offline-tool-routing test is what catches the whole class of
   P0-3. Suite gets ~13s faster as a side effect.
2. **Session 2 (~2–3 hours):** P1b (`.dockerignore`, frontend CI job, Python
   matrix, `--frozen`, concurrency/cache/timeout) — stop broken artifacts from
   merging. Then P1a's quick ones (timeout, `max_length`, httpx dependency).
3. **Session 3 (~1 day):** coverage floor + test-helper consolidation +
   first frontend tests (`markdown.ts` first).
4. **Session 4+:** P2b refactors, one at a time behind the now-stronger suite.
5. **Ongoing:** P3 items as they become relevant.

## What the review found already good (don't over-correct)

Test hermeticity is excellent (`HermeticSettings` makes local `.env` leakage
impossible; the `settings` fixture parametrizes the whole suite over three
backends for free). `.pre-commit-config.yaml` deliberately uses `local` hooks
via `uv run` to avoid CI version drift. The Dockerfile is properly
multi-stage and uses `uv sync --frozen` — stricter than CI. Pyright covers
`tests/` and `evals/`, not just `src/`. Zero `TODO`/`FIXME` markers. The
`AgentBackend`/`AgentEvent` contracts are clean, and reusing agent event
models as WS wire frames genuinely prevents protocol drift. `Tool.run` is a
well-chosen single telemetry seam and its broad `except` is *correct*.
`current_turn_stats` contextvar handling is right (set + `reset` in `finally`).
`observability.py`'s lazy imports / inert-by-default posture and `logs.py`'s
single structlog pipeline are both well done. And the Groq key never entered
git history.
