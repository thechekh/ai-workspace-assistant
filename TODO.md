# TODO / Roadmap — current stage & what's next

Updated: 2026-08-06. Phase-by-phase build history with acceptance evidence
lives in [implementation-plan.md](implementation-plan.md); this file tracks
what's *next*.

## Where the project stands

All 8 planned phases are **complete**:

| Area | State |
|---|---|
| Chat | Streaming WebSocket chat, typed protocol, Redis sessions, resume on reconnect |
| Agents | 3 runtimes (custom / Pydantic AI / LangGraph) behind one protocol, switchable per session |
| RAG | Hybrid (dense+sparse RRF) + rerank — golden set recall@1 0.83 / recall@5 1.00 / MRR 0.92 |
| MCP | Client registry + 2 bundled stdio servers (real code-search, mock GitHub with real tool names) |
| Memory | Rolling summarization — prompts provably stop growing (tested ×3 backends) |
| Platform | taskiq worker + nightly re-index, optional bearer auth, /api/info + /api/reindex, Vue UI |
| Observability | structlog JSON + correlation IDs, manual OTel spans → Jaeger, /metrics + Grafana dashboard, deep health, audit trail, per-turn stats in UI |
| Quality | 90 deterministic tests, ruff + pyright clean, CI green on GitHub |
| Docs | README, tech-stack, backend comparison, workshop script, 13-chapter theory course |
| Git | https://github.com/thechekh/ai-workspace-assistant (private), 11 per-area commits, Actions ✅ |

Known caveats (tracked below): full compose `--profile app` build unverified
(Docker Desktop/disk issues on this machine); sessions sidebar descoped;
cloud tracing wired but unverified (no tokens yet).

## Recently done

- [x] Real-provider hardening + cost + explain-turn panel, verified live
      against Groq + Docker Qdrant/Redis + Jaeger: 429 backoff, Groq
      `tool_use_failed` retry & leaked-tool-call salvage, friendly WS errors,
      $-per-turn, UI timeline panel, docs/tools.md + docs/testing.md
      *(2026-08-07)*
- [x] Phase 9 Tiers 1–3: structured logs + correlation IDs, spans → Jaeger,
      /metrics + Grafana, deep health, audit trail, per-turn UI stats
      *(2026-08-06)*
- [x] Git commits + GitHub push + CI green *(2026-08-06)*

---

## Phase 9: Maximum observability — Tiers 1–3 DONE *(2026-08-06)*

Goal: **every process explained, traceable, and controlled** — see exactly
what the agent did, why, how long it took, and what it cost. Offline-first
(same philosophy as the rest of the project): Tiers 1–3 need zero accounts.

### Tier 1 — Foundations (structured logs + correlation; no new services) ✅

- [x] **Structured logging (structlog)**: `logs.py` — pretty console in dev,
      JSON lines with `ASSISTANT_LOG_JSON=true`; one pipeline for our loggers
      and stdlib/uvicorn loggers.
- [x] **Correlation IDs everywhere**: `turn_id` per user message;
      `session_id` + `turn_id` + `backend` bound via structlog contextvars —
      every log from the loop, tools, RAG carries them automatically; the
      `turn` WS frame carries `turn_id` so UI events map to server logs.
- [x] **Turn summary line**: one `turn.summary` log per turn — duration,
      first-token ms, LLM steps + ms, tool calls, tokens, answer size
      (retrieval details on the correlated `rag.retrieved` line).
- [x] **Deep health endpoint**: `/api/health` — Redis ping + Qdrant count
      (with latency), LLM provider/model, MCP tools; green/amber/gray dot in
      the UI header, refreshed every 10 s.
- [x] **Agent event audit trail**: per-turn record (stats + event timeline)
      in Redis, capped at 50/session → `GET /api/sessions/{id}/turns`.

### Tier 2 — Real tracing, still no accounts ✅

- [x] **Local trace UI via Jaeger**: compose `observability` profile;
      `ASSISTANT_OTLP_ENDPOINT=http://localhost:4318` exports spans →
      waterfalls at `localhost:16686`, zero signups.
- [x] **Manual spans on the seams**: `agent.turn` → `llm.step` (provider,
      model, tokens, tool calls) / `tool.execute` (tool, status, result
      size) / `rag.retrieve` (mode, candidates, top score) — traces explain
      the agent, not just HTTP.
- [x] **Token/usage capture**: `stream_options.include_usage` on
      OpenAI-compatible streams (with reject-retry fallback) → UsageEvent →
      spans, metrics, turn summary; chars/4 estimate otherwise, flagged
      `usage_estimated`.
- [x] **Prompt/response debug toggle**: `ASSISTANT_LOG_PROMPTS=true` dumps
      full prompts + completions (dev-only; privacy note in config).

### Tier 3 — Metrics & dashboards ✅

- [x] **/metrics (Prometheus)**: turns/errors/tool-calls/tokens counters +
      turn/LLM-step/tool/retrieval latency histograms, labeled by
      backend/provider/tool/status/mode.
- [x] **Grafana** provisioned dashboard (turn rate + p50/p95 by backend, LLM
      p95 by provider, tokens/min, tool calls + p95, retrieval p95, errors).
- [x] **Cost accounting**: price table → `cost_usd` per turn (stats line,
      audit record, `assistant_cost_usd_total{model}` counter; per-day via
      Prometheus `increase()`). Indicative $ at listed prices; free tier
      bills $0.

### Tier 4 — Product-level visibility (remaining)

- [x] **Per-turn stats in the UI**: `turn` WS frame → stats line under each
      answer (duration, first token, steps, tokens real/est, cost, tools).
- [x] **"Explain this turn" panel in the UI**: `details` under each answer
      expands the audit timeline (tool_call/tool_result/final with +ms).
- [ ] **Eval trend history**: append eval runs to `evals/history.jsonl`
      with timestamp + config; print deltas — regressions become visible.
- [ ] **Cloud backends when tokens exist**: Logfire + Langfuse share the
      pipeline (`observability.py`) — add tokens, verify dashboards, done.

### Provider hardening (added 2026-08-07, verified live vs Groq) ✅

- [x] **429 backoff-retry** honoring Retry-After; friendly rate-limit error
      frame; `assistant_errors_total{kind}` for every failure class.
- [x] **Groq `tool_use_failed` recovery**: retry the step (2×), then salvage
      the call from `failed_generation`; leaked `<function…>` text output is
      parsed into real tool calls instead of reaching the chat.
- [x] **Clear WS errors** for auth (401/403), missing model (404), provider
      5xx, unreachable provider — mapped from any backend's exception chain.

---

## Backlog — features

- [ ] **Your side (.env, minutes each)**: ~~Groq key → real model in the
      demo~~ *(done 2026-08-07 — key in `.env`, verified end-to-end)*;
      OpenAI key → real rows in the embedding comparison table
      (`python -m evals.compare_embeddings`); GitHub PAT + one config line →
      real GitHub MCP instead of the mock.
- [ ] **Interrupt/cancel button** — bidirectional-WS showcase; touches the
      agent loop meaningfully. *Recommended as the guided task you build
      yourself, with review.*
- [ ] **Sessions sidebar** — session-listing API (Redis scan) + UI panel;
      the one descoped Phase-8 item.
- [ ] **Verify full compose build** — after fixing Docker Desktop + disk:
      `docker compose --profile app up --build` (last unverified box).
- [ ] **LangGraph Redis checkpointer** — makes its flagship persistence
      feature real (durable, resumable runs).
- [ ] **Long-term memory facts store** — distilled facts in Qdrant,
      retrieved like RAG across sessions.
- [ ] **OIDC/SSO** — replace the demo bearer token at the gateway.
- [ ] **Rate limiting / per-user quotas**.

## Learning track (workshop prep)

- [ ] Implement one change end-to-end yourself (interrupt button or a new
      tool) with review — touches every layer once.
- [ ] Interactive code-reading sessions (pick a file, interrogate it).
- [ ] Mermaid sequence diagrams in the theory chapters (also slide-ready).
- [ ] Mock Q&A rehearsal against [theory/12-defense-qa.md](theory/12-defense-qa.md).
