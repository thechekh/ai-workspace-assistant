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
| Quality | 212 Python tests (83.8% coverage, floor enforced) + 16 frontend tests; ruff (strict rules) + pyright clean |
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

## A. Code quality — structural refactors ✅ *(all done 2026-08-08)*

Every item in this section is complete; details are in "Completed in the
code-quality pass" below. Summary: TurnRecorder extracted from `_handle_turn`
(154→81 lines), `stream_step` split into a retry shell plus two helpers,
`build_runtime()` extracted from `create_app` (109→62), backend duplication
removed, one pooled HTTP client, typed audit wire contract with a per-turn
endpoint, `agent/tools.py` split into a package, `llm/errors.py` extracted,
and blocking CPU/IO moved off the event loop.

## B. Quality tooling — remaining

- [x] **Security scanning** ✅ *(2026-08-08)* — `.github/workflows/security.yml`:
      CodeQL (python + javascript-typescript), `pip-audit` over the resolved
      runtime tree, `npm audit`, weekly schedule. It immediately found and we
      fixed: happy-dom RCE (critical), pydantic-ai SSRF + path traversal,
      fastmcp command injection, diskcache pickle. Both audits now clean.
      *(Container image scanning and secret scanning are still not wired.)*
- [ ] **CodeQL needs GitHub Advanced Security.** The workflow is written and
      the analysis runs, but uploading results to a **private** repo requires
      GHAS, so the job is gated on `github.event.repository.private == false`
      and currently skips. It self-enables when the repo goes public or GHAS
      is turned on. `pip-audit`/`npm audit` run unconditionally.
- [ ] **Raise the coverage floor** — currently pinned at 82% (measured 83.8%).
      Ratchet upward as gaps close; never lower it.
- [ ] **Prettier/ESLint for TS/Vue** — nothing formats or lints the frontend;
      pre-commit and CI only cover Python.
- [ ] **CI should run `pre-commit run --all-files`** so contributors who skip
      `pre-commit install` are still caught.
- [ ] **`pyright` strict mode** — currently `standard`; nearly free on an
      already-clean codebase.
- [ ] **`pytest-xdist`** — the suite looks xdist-ready (no shared files,
      per-test in-memory stores, `tmp_path` used correctly). 292 tests in ~22s
      is fine today, so this is a later-scale item.
- [ ] **Decide one Python version.** There are currently three: local venv
      **3.14**, CI matrix **3.12 + 3.13**, Docker image **3.13**. A
      `.python-version` file was tried and reverted — it forces uv to rebuild
      the local venv, which failed on locked files. Decide the target, then
      add the file deliberately.
- [ ] **TypeScript 7.** On **6.0.3** — the last JS-based compiler, which is
      what `vue-tsc` supports; TS 7's native rewrite ships no compiler API
      yet (expected 7.1), and vue-tsc embeds the compiler to split SFCs into
      virtual files, so it cannot move until then. 6.0 also lands the
      deprecations as warnings, making the eventual 7 jump nearly free — our
      tsconfig already satisfies its stricter defaults. If a non-SFC package
      ever wants TS 7's `tsc`, alias it:
      `"@typescript/native": "npm:typescript@^7"` +
      `"typescript": "npm:@typescript/typescript6@^6"` — no benefit here
      today since `vue-tsc --noEmit` covers the whole frontend.
      Watch vuejs/language-tools (#6123 already merged TS 7 support).

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
      resolution); rate limits are per session, not per user (there is no user
      identity until OIDC); unauthenticated GitHub API (60 req/h, falls back
      to HTML — consider an optional `ASSISTANT_GITHUB_TOKEN`).

## D. Features

- [ ] **Your side (.env, minutes each)**: ~~Groq key~~ *(done)*; OpenAI key →
      real rows in the embedding comparison
      (`python -m evals.compare_embeddings`); GitHub PAT + one config line →
      real GitHub MCP instead of the mock.
- [x] **Interrupt/cancel button** — `{"type": "cancel"}` frame, turns run as
      their own task, Stop button + `Esc`; partial answer and partial cost are
      both kept. *(done)*
- [x] **Sessions panel** — `GET /api/sessions` off a sorted-set recency index
      (no keyspace scan), transcript restore, delete; the descoped Phase-8
      item. *(done)*
- [x] **Eval trend history + CI gate** — `--record` to `evals/history.jsonl`,
      `--trend` to read it, `--check` against `evals/baseline.json` as a CI
      job. Re-running the ablation found the documented dense/hybrid baselines
      had been stale since the relevance gate landed. *(done)*
- [ ] **Cloud tracing backends when tokens exist** — Logfire + Langfuse share
      the pipeline (`observability.py`); add tokens, verify dashboards.
- [ ] **LangGraph Redis checkpointer** — makes its flagship persistence
      feature real (durable, resumable runs).
- [ ] **Long-term memory facts store** — distilled facts in Qdrant, retrieved
      like RAG across sessions.
- [ ] **OIDC/SSO** — replace the demo bearer token at the gateway. Also the
      prerequisite for per-*user* quotas: the limiter below already exists,
      it just has no user to key on.
- [x] **Rate limiting** — sliding windows in Redis, per session for turns and
      per caller for indexing writes, refused before any LLM call. *(done)*

## E. Learning track (workshop prep)

- [ ] Implement one change end-to-end yourself (a new tool is the natural
      candidate now that the interrupt button is built) — touches every layer
      once.
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
  Also the reason 12.5s of the ~13s suite was real sleeping — the suite is now
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

- **Coverage measurement added**: 82.7% at the time, floor enforced at 82% in CI (83.8% today).
- **16 frontend tests** (vitest + happy-dom): `markdown.ts` sanitization —
  the app's main XSS surface, since it renders model output — and the Pinia
  WS-event reducer.
- Test helpers (`ScriptedLLM`, `make_registry`, `collect_until_final`,
  `make_client`) moved into `conftest.py`; test modules no longer import each
  other.
- pytest: `--strict-markers`/`--strict-config`, `filterwarnings = error`,
  explicit asyncio loop scope, and a `slow` marker for the subprocess-spawning
  MCP tests (`pytest -m "not slow"` → 238 tests in ~11s).
- ruff: added `S` (bandit), `PT`, `LOG`, `G`, `T20` with scoped per-file
  ignores. Findings fixed properly rather than suppressed — md5 marked
  `usedforsecurity=False`, type-narrowing `assert`s replaced with `cast` (they
  vanish under `python -O`).
- pre-commit: `check-yaml`/`toml`/`json`, `end-of-file-fixer`,
  `trailing-whitespace`, `detect-private-key`, `check-merge-conflict`,
  `check-added-large-files`.
