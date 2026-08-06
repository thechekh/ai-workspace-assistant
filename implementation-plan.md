# Implementation Plan — AI Workspace Assistant

Simple → complex, one phase at a time. Working agreement:

- Every phase ends **green**: `ruff check`, `ruff format --check`, `pyright`, `pytest` all pass.
- Short-lived feature branches per phase (`feat/phase-2-rag`), merged to `main` when green.
- The three agent backends (custom / Pydantic AI / LangGraph) **co-exist in `main`** behind the `ASSISTANT_AGENT_BACKEND` config switch — never long-lived divergent branches.
- Zero-cost development first: the `fake` LLM provider (offline, deterministic) is the default; Groq free tier when a real model is needed; the $25 OpenAI budget only for final quality testing.

Status: `[x]` done · `[~]` in progress · `[ ]` pending

---

## Phase 0 — Scaffolding ✅

Goal: a modern Python project skeleton that installs, lints, type-checks, and tests in one command each.

- [x] `git init` (branch `main`), `.gitignore`
- [x] uv project: `pyproject.toml`, src layout (`src/assistant/`), deps: fastapi, uvicorn[standard], pydantic, pydantic-settings, redis, openai
- [x] Dev deps: ruff, pyright, pytest, pytest-asyncio, httpx, respx, fakeredis, pre-commit
- [x] ruff (lint + format) and pyright config in `pyproject.toml`
- [x] `.pre-commit-config.yaml` (local ruff hooks via uv — no version drift)
- [x] `docker-compose.yml`: redis + qdrant with volumes
- [x] `.env.example` with all `ASSISTANT_*` variables
- [x] GitHub Actions CI: ruff → pyright → pytest
- [x] `README.md` quickstart

**Acceptance:** `uv sync` succeeds; `uv run ruff check .`, `uv run pyright`, `uv run pytest` green; `docker compose up -d` starts redis + qdrant.

---

## Phase 1a — WebSocket chat core (backend) ✅

Goal: a working streaming chat over WebSocket with session memory — no tools yet, but every interface the later phases need.

- [x] `config.py`: `Settings` (pydantic-settings, `ASSISTANT_` prefix, `.env` support)
- [x] `api/schemas.py`: typed WS protocol — client `user_message`; server `session`, `token`, `tool_call`, `tool_result`, `final`, `error` (discriminated unions)
- [x] `agent/base.py`: `ChatMessage`, `AgentEvent` models, `AgentBackend` protocol — the contract all three backends implement
- [x] `llm/client.py`: `LLMClient` protocol + `OpenAICompatibleLLM` (one client for groq/ollama/gemini/openai via base_url) + `FakeLLM` (offline deterministic provider, default)
- [x] `memory/session.py`: `SessionStore` — Redis-backed history with TTL
- [x] `agent/backends/custom.py`: CustomAgent v1 — streaming chat (tool loop arrives in Phase 3)
- [x] `agent/registry.py`: settings → backend factory (`custom` now; `pydantic_ai`/`langgraph` raise until their phases)
- [x] `api/ws.py`: `/chat` WS endpoint — session bootstrap, event forwarding, history persistence, error frames that don't kill the socket
- [x] `main.py`: `create_app()` factory (injectable redis/llm/agent for tests), lifespan wiring, `/healthz`, `/dev` console page
- [x] `static/dev.html`: minimal browser console for manual WS testing

**Acceptance:** `uv run uvicorn assistant.main:app` + open `/dev` → streaming chat works with `fake` provider (no API key, no cost); same code streams from Groq when a key is set; tests green.

---

## Phase 1b — Vue 3 client ✅

Goal: replace the dev console with a real SPA.

- [x] Scaffold `frontend/`: Vite + Vue 3 + TypeScript
- [x] WS client with `useWebSocket` (@vueuse/core): auto-reconnect, session resume via `?session_id=`
- [x] Pinia store: session id, message list, streaming buffer, backend selection
- [x] Chat UI: streamed tokens rendered as Markdown (markdown-it + highlight.js, `html: false`)
- [x] Tool-call cards component (placeholder until Phase 3)
- [x] Agent-backend switcher dropdown (sends `?backend=` — wired fully in Phase 5/6)
- [x] Vite dev proxy (`/chat` ws → :8000); production build served by FastAPI `StaticFiles`
- [x] Bonus: `ASSISTANT_REDIS_URL=fakeredis://` — zero-infrastructure dev mode (in-memory sessions)

**Acceptance (verified):** `npm run build` green (vue-tsc + vite); SPA served at `/` by the backend; browser E2E via Playwright — message sent, reply streamed, `**markdown**` rendered as `<strong>`.

---

## Phase 2 — RAG pipeline ✅

Goal: the assistant can answer from *our* docs.

- [x] Add `qdrant-client`; `docs_corpus/` sample docs (architecture, guidelines, onboarding — 5 docs)
- [x] `rag/chunking.py`: heading-aware Markdown chunking with breadcrumb prefixes, code-fence-safe, deterministic uuid5 ids (idempotent re-ingest)
- [x] `rag/embeddings.py`: `Embedder` protocol — offline `hash-512` feature-hashing embedder (zero-cost dev/test default) + OpenAI `text-embedding-3-small` (voyage-3 added in Phase 7)
- [x] `rag/ingest.py`: CLI `uv run python -m assistant.rag.ingest docs_corpus` → parse → chunk → embed → upsert
- [x] `rag/retriever.py`: top-k search with payload (source) filters
- [x] Golden question set v1 (`evals/golden.yaml`, 18 questions → expected source + text)
- [x] `evals/run_retrieval.py`: recall@1 / recall@k / MRR report

**Acceptance (verified):** 30 chunks ingested into the Qdrant container end-to-end; retriever tested against in-memory Qdrant (`:memory:`) + hash embedder — 20/20 tests green. Golden-set baseline with the free offline `hash-512` embedder: **recall@1 0.56, recall@5 0.94, MRR 0.72**. The single miss ("What linter and formatter do we use?" — lexical gap: *linter* vs *lint*) is exactly the case semantic embeddings fix → motivates the Phase 7 comparison.

---

## Phase 3 — Custom agent tool loop ✅

Goal: the real ReAct loop — the model decides when to call tools.

- [x] `LLMClient.stream_step()`: streamed `TextDelta` + accumulated `ToolCallRequest` events (OpenAI tools API); `ChatMessage` extended with `tool` role + `tool_calls`
- [x] `agent/tools.py`: `Tool` + `ToolRegistry` shared by all backends; first tool: `search_docs` (RAG retriever, source-tagged results)
- [x] CustomAgent v2: loop — LLM → tool_calls? → execute → append results → repeat, bounded by `max_iterations`; crashing/unknown tools become error results, not exceptions
- [x] WS `tool_call` / `tool_result` events wired through; UI cards render them
- [x] FakeLLM plays a one-round agent offline (question → search_docs → grounded answer) — the whole demo runs at $0 with no API key
- [x] Tests: scripted LLM — loop executes+finalizes, max-iterations bound, unknown tool, malformed JSON args, crashing tool; WS test asserts tool events + grounded final
- [x] Hardening (found by E2E): a Redis/Qdrant outage now sends an error frame instead of silently killing the socket

**Acceptance (verified):** 26/26 tests green; browser E2E against real Redis + Qdrant — "What is our deployment architecture?" rendered a `search_docs` tool card, the result card showed retrieved chunks from `architecture/deployment.md` (ArgoCD CI/CD, environments), and the answer was grounded in them.

---

## Phase 4 — MCP integration ✅

Goal: tools come from MCP servers, not just local functions. Built **credential-free**: everything runs locally with zero env vars; real GitHub is a config swap later.

- [x] `mcp` SDK (2.0); `mcp/registry.py` — connects configured servers (stdio + streamable HTTP), namespaces tools (`code__search_code`, `github__list_pull_requests`), adapts them into the shared `ToolRegistry` (backends can't tell MCP tools from native ones)
- [x] Bundled MCP server `assistant.mcp_servers.code_search`: `search_code` (pure-python regex over the repo — no ripgrep dependency) + `read_file` with path-traversal guard
- [x] Bundled **mocked** GitHub MCP server `assistant.mcp_servers.fake_github`: same tool names as the official `ghcr.io/github/github-mcp-server` (`list_pull_requests`, `get_pull_request`, `list_issues`) with realistic canned data — swapping to the real server later is only an `ASSISTANT_MCP_SERVERS` config change (documented in .env.example)
- [x] Graceful degradation: unreachable/disabled server → warning log, agent runs with the remaining tools (tested)
- [x] FakeLLM heuristics route PR and "search code" phrasings to the MCP tools — full offline demo
- [x] Servers configurable via `ASSISTANT_MCP_SERVERS` JSON; `{python}` placeholder resolves to the venv interpreter

**Acceptance (verified):** 29/29 tests green (real stdio integration test spawns both servers and executes tools; degradation test). Browser E2E: "Show latest PRs in the repo" rendered a `github__list_pull_requests` card with the mocked PRs; "search code for class CustomAgent" rendered a `code__search_code` card that found `src/assistant/agent/backends/custom.py:38` — a real regex hit in this repository.

---

## Phase 5 — Pydantic AI backend + observability ✅

- [x] `pydantic-ai` 1.47; `agent/backends/pydantic_ai.py` — same `AgentEvent` stream via the graph iteration API (`agent.iter`), same shared ToolRegistry via `Tool.from_schema`
- [x] MCP tools flow in through the shared registry (deliberate change from "native pydantic-ai MCP client": one tool source keeps all backends identical for the comparison; native MCP support noted as the framework's alternative)
- [x] `fake` provider → pydantic-ai `FunctionModel` twin of FakeLLM — backend runs offline at zero cost; hosted providers reuse the same base-url map as the custom client
- [x] `observability.py`: Logfire (`instrument_fastapi` + `instrument_httpx` + `instrument_pydantic_ai`) with Langfuse OTLP forwarding as an additional span processor — **fully inert without tokens** (lazy imports, no OTel globals in tests)
- [x] Per-session backend override: WS `?backend=` param; UI dropdown reconnects with the same session id (history preserved across runtime switch)
- [x] WS test suite parametrized over both backends — identical assertions pass on both

**Acceptance (verified):** 40/40 tests green (whole WS suite × custom + pydantic_ai, plus direct backend tests: streaming parity, history parity, tool loop). Browser E2E: switched the dropdown to pydantic-ai — server log shows reconnect `?backend=pydantic_ai` with the same session id — and the docs question ran the `search_docs` tool with billing-service retrieved at rank 1. Trace verification in Logfire/Langfuse dashboards deferred until tokens exist (wiring inert by design until then).

---

## Phase 6 — LangGraph backend + comparison ✅

- [x] `langgraph` 1.2; `agent/backends/langgraph.py` — explicit two-node StateGraph (`agent` ⇄ `tools`), same `AgentBackend` contract, streaming via `stream_mode=["messages", "updates"]`
- [x] `LLMClientChatModel`: a ~95-line `BaseChatModel` adapter over our `LLMClient` protocol — every provider (fake/Groq/…) and every scripted test LLM runs on LangGraph unchanged
- [x] Tools node executes through the shared `ToolRegistry` (identical capabilities across backends)
- [x] Checkpointing: compiled with `InMemorySaver`, fresh `thread_id` per turn — native LangGraph persistence demonstrated while cross-turn memory stays in shared Redis
- [x] Loop bound: `recursion_limit` → `GraphRecursionError` → same "limit" final message as the other backends
- [x] `docs/backend-comparison.md` — measured LoC (custom 103 / pydantic-ai 209 / langgraph 282), adapter costs, streaming/tooling/memory/observability/debuggability dimensions, verdict table

**Acceptance (verified):** 52/52 tests green — the whole WS suite parametrized ×3 backends, plus 5 direct LangGraph tests (streaming parity, history parity via the same "(N messages in context)" accounting, scripted tool loop, recursion bound, RAG roundtrip). Browser E2E: dropdown switched to langgraph (server log: reconnect `?backend=langgraph`, same session id) and "Show latest PRs" ran the MCP tool card end-to-end on the LangGraph runtime.

---

## Phase 7 — Memory & retrieval upgrades ✅

- [x] Conversation summarization: `ConversationMemory` folds over-budget history into a **persisted rolling summary** (each message summarized once); context = system + summary + recent turns verbatim. Offline `ExtractiveSummarizer` (fake provider) + `LLMSummarizer` for real models; all three backends accept the summary system message
- [x] Hybrid search: sparse lexical vectors (stable 32-bit token hashing, TF weights) alongside dense in one named-vector collection; RRF fusion via the Qdrant Query API; auto-recreates old single-vector collections
- [x] Deterministic `LexicalReranker` over the top-20 (Reranker protocol ready for voyage/Cohere API rerankers)
- [x] `VoyageEmbedder` (raw httpx, respx-tested) — key-gated per the no-envs rule
- [x] `evals/compare_embeddings.py`: auto-detects available providers (hash always; openai/voyage when keys exist), per-model `:memory:` collections, writes `evals/results-embeddings.md`
- [x] `run_retrieval.py --memory`: fully self-contained eval (in-process Qdrant + on-the-fly ingest — no Docker)

**Acceptance (verified):** 65/65 tests green (summarizer units, incremental folding, bounded-context WS test ×3 backends — prompt size provably stops growing; sparse/hybrid/reranker/voyage tests). Golden-set numbers (hash-512, 18 questions): dense baseline 0.56/0.94/0.72 → hybrid 0.67/1.00/0.80 → **hybrid+rerank 0.83/1.00/0.92** (recall@1/recall@5/MRR) — every question now lands in the top 5, at zero cost. Comparison table in `evals/results-embeddings.md`; openai/voyage rows appear automatically once keys exist.

---

## Phase 8 — Platform polish ✅

- [x] taskiq + taskiq-redis: `assistant.worker` with `reindex_docs` task + nightly cron (03:00) via `TaskiqScheduler`; `POST /api/reindex` queues it (or runs inline in zero-infra `fakeredis://` mode) — UI Re-index button included
- [x] Optional bearer-token auth (`ASSISTANT_AUTH_TOKEN`): `/api/*` via Authorization header, chat WS via `?token=` (closed 1008 otherwise); unset = open for zero-config dev; OIDC noted as the production path
- [x] `GET /api/info`: platform shape for the UI (backends, providers, retrieval mode, auth flag)
- [x] UI polish: SVG favicon (console 404 gone), provider badge ("fake · hybrid" + collection tooltip), transient ok/error toasts (WS errors + reindex results), Re-index button; token capture from `?token=` persisted locally. *(Sessions sidebar descoped — needs a session-listing API; noted as future work.)*
- [x] Full-platform compose: `docker compose --profile app up` adds api + worker + scheduler (one multi-stage image: Vue build → uv runtime); plain `docker compose up -d` still starts just redis+qdrant for dev
- [x] `docs/workshop.md`: Part 1 slide outline (with our measured numbers), Part 2 click-by-click demo script (offline-capable), Part 3 implementation walkthrough map

**Acceptance (verified):** 72/72 tests green (info shape, inline reindex, 503 path, bearer auth on HTTP + WS ×token cases); browser E2E on the zero-infra path — favicon loads clean, badge renders from /api/info, Re-index button fires and the error toast appears and auto-dismisses (Qdrant intentionally down). *Caveat:* the compose `app` profile is written but **not built on this machine** — Docker's engine was down and disk space was tight during this phase; run `docker compose --profile app up --build` once Docker is healthy.
