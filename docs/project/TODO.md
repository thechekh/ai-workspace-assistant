# TODO / Roadmap — current stage & what's next

Updated: 2026-08-08. The single backlog for this project: features, code
quality, and hardening in one place. Phase-by-phase build history with
acceptance evidence lives in [implementation-plan.md](implementation-plan.md).

**Status legend:** ✅ verified by running it · 🔍 reported by review, not yet
re-verified.

## Where the project stands

All 9 planned phases are **complete**, plus a code-quality pass:

| Area | State |
|---|---|
| Chat | Streaming WebSocket chat, typed protocol, Redis sessions, resume on reconnect |
| Agents | 3 runtimes (custom / Pydantic AI / LangGraph) behind one protocol, switchable per session, offline parity tested |
| RAG | Hybrid (dense+sparse RRF) + rerank + relevance gate — golden set recall@1 0.83 / recall@5 1.00 / MRR 0.92 |
| Tools | `search_docs`, `fetch_url` (web + GitHub API), 2 MCP servers; per-turn duplicate guard; one telemetry seam |
| MCP | Client registry + 2 bundled stdio servers (real code-search, mock GitHub with real tool names) |
| Memory | Rolling summarization — prompts provably stop growing (tested ×3 backends) |
| Platform | taskiq worker + nightly re-index, optional bearer auth, /api/info + /api/reindex, Vue UI |
| Observability | structlog JSON + correlation IDs, OTel spans → Jaeger, /metrics + Grafana, deep health, audit trail, per-turn stats + cost in UI |
| Provider hardening | 429 backoff, Groq `tool_use_failed` retry + salvage, leaked-tool-syntax parsing, friendly WS errors, 60s timeout |
| Quality | 145 Python tests (82.7% coverage, floor enforced) + 16 frontend tests; ruff (strict rules) + pyright clean |
| CI | Python 3.12 **and** 3.13 matrix, frontend typecheck/test/build, Docker image build, coverage gate, dependabot |
| Docker | Multi-stage build ✅ verified; non-root; healthchecks; pinned tags; `--profile app` stack ✅ verified end-to-end |
| Docs | All under [docs/](../README.md): [handbook](../handbook/README.md) (9 chapters), [theory](../theory/README.md) course (13), [reference](../reference/tools.md), project/ |
| Git | https://github.com/thechekh/ai-workspace-assistant (private), CI green |

## Recently done

- [x] **Code-quality pass** *(2026-08-08)* — 3 P0 bugs fixed, robustness +
      build/CI hardening, coverage gate, frontend tests. Details below under
      "Completed in the code-quality pass".
- [x] Real-provider hardening + cost + explain-turn panel, verified live
      against Groq + Docker Qdrant/Redis + Jaeger *(2026-08-07)*
- [x] `fetch_url` tool + retrieval relevance gate + duplicate-call guard —
      fixed garbage RAG hits and hallucinated URLs *(2026-08-07)*
- [x] Phase 9 Tiers 1–3: structured logs + correlation IDs, spans → Jaeger,
      /metrics + Grafana, deep health, audit trail, per-turn UI stats
      *(2026-08-06)*

---

# Active backlog

## A. Code quality — structural refactors

Deferred deliberately: these are safe to do now that the suite is stronger,
but none of them is letting a bug through today.

- [ ] **`_handle_turn` is 154 lines with 8 positional parameters** 🔍
      — [api/ws.py:151](../../src/assistant/api/ws.py#L151). Owns turn-id,
      contextvars, span attrs, memory fetch, a 5-branch dispatch that
      simultaneously forwards frames *and* builds audit dicts *and* counts
      tokens, error classification, cost math, summary assembly, logging, and
      Redis persistence.
      **Fix:** extract a `TurnRecorder` (`observe(event)` + `summary()`) and
      `_persist_turn(...)`; pass a `TurnContext` dataclass instead of 8 args.

- [ ] **`stream_step` is ~96 lines with two interleaved retry mechanisms** 🔍
      — [llm/client.py:207](../../src/assistant/llm/client.py#L207). Six mutable
      flags across `while True` → `async for` → `except APIError`; `pending`/
      `usage`/`held` are initialized twice.
      **Fix:** extract `_LeakedTextBuffer` and `_ToolCallAccumulator`, leaving
      `stream_step` as the retry shell. Use
      `async with await self._create_stream(...) as stream:` so the HTTP
      response closes deterministically rather than at asyncgen GC.

- [ ] **`create_app` is 109 lines with a 68-line nested `lifespan`** 🔍
      — [main.py:42](../../src/assistant/main.py#L42). Test-injection params gate
      production wiring (passing `agent=` disables both Qdrant and MCP).
      **Fix:** a `build_runtime(settings) -> Runtime` that tests override
      wholesale.

- [ ] **Duplicated constants and builders across the three backends** 🔍
      — `_EVENT_RESULT_LIMIT = 1500` in all three backends (three backends can
      silently diverge on how much tool output the UI sees — the same bug
      class as the fake-LLM drift already fixed); `_to_openai_tools`
      duplicated inline in `langgraph.py:171-181`; dead
      `if self._tools is None` branches in `custom.py` and `langgraph.py`
      (unreachable since `fetch_url` is always registered).
      **Fix:** `truncate_for_event()` in `agent/base.py`; reuse
      `_to_openai_tools`; make `ToolRegistry` non-optional and delete the
      dead branches.

- [ ] **HTTP clients created per call** 🔍 — `make_fetch_url` builds a fresh
      `httpx.AsyncClient` per invocation ([tools/fetch.py](../../src/assistant/agent/tools/fetch.py)),
      so every call pays a TCP+TLS handshake (twice on the GitHub path); same
      in `VoyageEmbedder.embed`. `OpenAIEmbedder._client` is never closed.
      **Fix:** one pooled client in the lifespan, injected; `aclose()` in the
      lifespan `finally`. (`OpenAICompatibleLLM.aclose()` now exists but is
      not yet called.)

- [ ] **`dict[str, object]` on real wire boundaries** 🔍 — audit records
      round-trip untyped through `SessionStore.append_turn` and out of
      `GET /api/sessions/{id}/turns`; `/api/info` and `/api/health` return
      bare dicts with no `response_model`. The audit schema is a real contract
      the frontend consumes.
      **Fix:** `TurnAuditEvent`/`TurnRecord` models beside `TurnSummary`; add
      `response_model=` to the three routes.

- [ ] **Module organization** 🔍
      — `_describe_llm_error` (50 lines of *provider* error classification)
      lives in the WebSocket transport and imports from the LLM package to do
      it → move to `assistant/llm/errors.py`.
      — The RAG relevance gate runs inside the tool handler → move into
      `Retriever.search` (the retriever shouldn't return chunks it knows are
      irrelevant).
      — `agent/tools.py` (277 lines) is a grab-bag: registry + telemetry seam
      + GitHub REST client + HTML stripper → split into `agent/tools/fetch.py`.

- [ ] **Blocking CPU/IO on the event loop** 🔍 — `HashEmbedder.embed` is
      `async` but never awaits (pure-Python loop, md5 per token);
      `load_chunks` does sync `read_text` per file; `POST /api/reindex` runs
      `ingest(...)` inline in fakeredis mode — all on the loop serving live
      chats. Latent today (corpus is 24 KB).
      **Fix:** `asyncio.to_thread(...)` around embed + chunk loading.

- [ ] **Details panel over-fetches: 50 turns to render 1** ✅
      — [stores/chat.ts:214](../../frontend/src/stores/chat.ts#L214) downloads the
      whole audit list and filters client-side.
      **Fix:** `GET /api/sessions/{id}/turns/{turn_id}` (or `?turn_id=`).

## B. Quality tooling — remaining

- [ ] **Security scanning** — dependabot now covers version bumps, but there
      is no CodeQL, `pip-audit`, `npm audit`, container scan, or secret scan.
      The app makes outbound fetches and spawns subprocesses. *(Highest-value
      item in this section.)*
- [ ] **Raise the coverage floor** — currently pinned at 82% (measured 82.7%).
      Ratchet upward as gaps close; never lower it.
- [ ] **Prettier/ESLint for TS/Vue** — nothing formats or lints the frontend;
      pre-commit and CI only cover Python.
- [ ] **CI should run `pre-commit run --all-files`** so contributors who skip
      `pre-commit install` are still caught.
- [ ] **`pyright` strict mode** — currently `standard`; nearly free on an
      already-clean codebase.
- [ ] **`pytest-xdist`** — the suite looks xdist-ready (no shared files,
      per-test in-memory stores, `tmp_path` used correctly). 145 tests in ~11s
      is fine today, so this is a later-scale item.
- [ ] **Decide one Python version.** There are currently three: local venv
      **3.14**, CI matrix **3.12 + 3.13**, Docker image **3.13**. A
      `.python-version` file was tried and reverted — it forces uv to rebuild
      the local venv, which failed on locked files. Decide the target, then
      add the file deliberately.

## C. Hardening & repo standards

- [ ] **Repo-standard files** — `LICENSE` (and `license`/`authors`/
      `classifiers` in `[project]`), `CONTRIBUTING.md`, `CHANGELOG.md`,
      `CODEOWNERS`, `SECURITY.md`, issue/PR templates, and a
      `CLAUDE.md`/`AGENTS.md` given how much of this was agent-built.
      *(LICENSE is a deliberate choice — pick the license yourself.)*
- [ ] **Grafana is fully open** (`GF_AUTH_ANONYMOUS_ENABLED` +
      `GF_AUTH_DISABLE_LOGIN_FORM`) — correct for local dev, must change if
      that compose file ever seeds a deployment.
- [ ] **Known-and-documented, revisit if this leaves localhost:** the
      `fetch_url` SSRF guard is dev-grade (string match on host, no DNS
      resolution); no API rate limiting; unauthenticated GitHub API (60 req/h,
      falls back to HTML — consider an optional `ASSISTANT_GITHUB_TOKEN`).

## D. Features

- [ ] **Your side (.env, minutes each)**: ~~Groq key~~ *(done)*; OpenAI key →
      real rows in the embedding comparison
      (`python -m evals.compare_embeddings`); GitHub PAT + one config line →
      real GitHub MCP instead of the mock.
- [ ] **Interrupt/cancel button** — bidirectional-WS showcase; touches the
      agent loop meaningfully. *Recommended as the guided task you build
      yourself, with review.*
- [ ] **Sessions sidebar** — session-listing API (Redis scan) + UI panel; the
      one descoped Phase-8 item.
- [ ] **Eval trend history** — append eval runs to `evals/history.jsonl` with
      timestamp + config; print deltas so regressions become visible. Then
      consider gating CI on retrieval quality.
- [ ] **Cloud tracing backends when tokens exist** — Logfire + Langfuse share
      the pipeline (`observability.py`); add tokens, verify dashboards.
- [ ] **LangGraph Redis checkpointer** — makes its flagship persistence
      feature real (durable, resumable runs).
- [ ] **Long-term memory facts store** — distilled facts in Qdrant, retrieved
      like RAG across sessions.
- [ ] **OIDC/SSO** — replace the demo bearer token at the gateway.
- [ ] **Rate limiting / per-user quotas**.

## E. Learning track (workshop prep)

- [ ] Implement one change end-to-end yourself (interrupt button or a new
      tool) with review — touches every layer once.
- [ ] Interactive code-reading sessions (pick a file, interrogate it).
- [ ] Mermaid sequence diagrams in the theory chapters (also slide-ready).
- [ ] Mock Q&A rehearsal against [the defense Q&A](../theory/12-defense-qa.md).

---

# Completed in the code-quality pass *(2026-08-08)*

Kept for the record — this is what a full-codebase review turned up and what
was done about it.

### Bugs found and fixed ✅

- **`retry-after: 0` was silently discarded.** `_retry_after_seconds(exc) or
  ...` treated a valid `0.0` ("retry now") as absent and slept 2s then 4s.
  Also the reason 12.5s of the ~20s suite was real sleeping — the suite is now
  **~9s**, and backoff is asserted rather than waited on.
- **Closing a browser tab was recorded as a server error.**
  `WebSocketDisconnect` subclasses `Exception`, so routine disconnects
  incremented `errors_total{kind="turn_exception"}` and logged tracebacks. Now
  re-raised ahead of the broad handler and logged as `turn.abandoned`.
- **The offline fake providers had drifted.** The pydantic-ai twin carried a
  hand-copied version of the demo heuristics that never learned `fetch_url`,
  so offline it behaved differently from the other two backends — undermining
  the backend comparison. Both now share
  [`llm/fake.py`](../../src/assistant/llm/fake.py), with
  [test_fake_parity.py](../../tests/test_fake_parity.py) asserting identical routing
  end-to-end on all three runtimes.
- **The Docker build was broken** (never previously verified): `pyproject`
  declares `readme = "README.md"` but the Dockerfile never copied it, so
  `uv sync` failed at the project-install step. Fixed, and the whole
  `--profile app` stack now verified running end-to-end with deep health `ok`.
- **Importing `assistant.main` had production side effects** — the
  module-level `app = create_app()` read `.env`, reconfigured global logging,
  and installed an OTLP tracer aimed at a developer's Jaeger (visible as
  export errors during test runs). Now built lazily via module `__getattr__`,
  so `assistant.main:app` still works unchanged for uvicorn.

### Robustness ✅

- 60s request timeout on the LLM client (was inheriting the SDK's **600s**
  read timeout) and SDK retries disabled so they don't multiply with ours;
  `aclose()` added.
- Telemetry moved into `try/finally` so an abandoned turn still records cost
  and latency instead of vanishing from metrics.
- `/api/health` now reports MCP as **degraded** when enabled servers failed to
  connect (it previously said `ok` with zero tools — exactly the case it
  exists to catch), including a `servers_connected: n/m` counter.
- `UserMessage.content` bounded to 8000 chars.
- `httpx` promoted from a transitive/dev dependency to a declared runtime one.

### Build & CI ✅

- `.dockerignore` added — build context was ~427 MB and included `.env`; the
  frontend stage was also copying the host's **Windows** `node_modules` over
  the container's Linux ones.
- Dockerfile: narrowed frontend copies, non-root `USER app`, `HEALTHCHECK`.
- CI: Python **3.12 + 3.13** matrix (the image ships 3.13 and was never
  tested), a frontend job (typecheck + tests + build), a Docker build job,
  `uv sync --frozen`, coverage gate, plus concurrency group, dep caching,
  `timeout-minutes`, and a read-only `permissions` block.
- `dependabot.yml` for uv, npm, docker, and github-actions.
- Compose: `env_file` so `--profile app` can actually reach a real LLM
  (verified: `provider: groq` through the container); healthchecks on
  redis/qdrant with `condition: service_healthy`; all image tags pinned
  (Qdrant matched to the installed client version); healthcheck disabled on
  worker/scheduler, which share the image but serve no HTTP.

### Tests & tooling ✅

- **Coverage measurement added**: 82.7%, floor enforced at 82% in CI.
- **16 frontend tests** (vitest + happy-dom): `markdown.ts` sanitization —
  the app's main XSS surface, since it renders model output — and the Pinia
  WS-event reducer.
- Test helpers (`ScriptedLLM`, `make_registry`, `collect_until_final`,
  `make_client`) moved into `conftest.py`; test modules no longer import each
  other.
- pytest: `--strict-markers`/`--strict-config`, `filterwarnings = error`,
  explicit asyncio loop scope, and a `slow` marker for the subprocess-spawning
  MCP tests (`pytest -m "not slow"` → 142 tests in ~4s).
- ruff: added `S` (bandit), `PT`, `LOG`, `G`, `T20` with scoped per-file
  ignores. Findings fixed properly rather than suppressed — md5 marked
  `usedforsecurity=False`, type-narrowing `assert`s replaced with `cast` (they
  vanish under `python -O`).
- pre-commit: `check-yaml`/`toml`/`json`, `end-of-file-fixer`,
  `trailing-whitespace`, `detect-private-key`, `check-merge-conflict`,
  `check-added-large-files`.
