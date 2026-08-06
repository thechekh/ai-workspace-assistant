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
| Quality | 72 deterministic tests, ruff + pyright clean, CI green on GitHub |
| Docs | README, tech-stack, backend comparison, workshop script, 13-chapter theory course |
| Git | https://github.com/thechekh/ai-workspace-assistant (private), 11 per-area commits, Actions ✅ |

Known caveats (tracked below): full compose `--profile app` build unverified
(Docker Desktop/disk issues on this machine); sessions sidebar descoped;
cloud tracing wired but unverified (no tokens yet).

## Recently done

- [x] Git commits + GitHub push + CI green *(2026-08-06)*

---

## Next milestone — Phase 9: Maximum observability

Goal: **every process explained, traceable, and controlled** — see exactly
what the agent did, why, how long it took, and what it cost. Offline-first
(same philosophy as the rest of the project): Tiers 1–2 need zero accounts.

### Tier 1 — Foundations (structured logs + correlation; no new services)

- [ ] **Structured logging (structlog)**: every log line as JSON (pretty in
      dev) with timestamp, level, logger — replacing ad-hoc std logging.
- [ ] **Correlation IDs everywhere**: generate a `turn_id` per user message;
      bind `session_id` + `turn_id` + `backend` into context so *every* log
      from the loop, tools, RAG, and MCP carries them automatically. Include
      `turn_id` in WS frames so UI events map to server logs.
- [ ] **Turn summary line**: one log per turn — backend, LLM steps, tool
      calls (names + durations), retrieval stats (chunks, top score),
      iterations used, total ms, prompt/answer sizes. The "what just
      happened" record, greppable.
- [ ] **Deep health endpoint**: `/api/health` checking Redis ping, Qdrant
      readiness, MCP servers connected, provider configured — surfaced as a
      green/yellow dot in the UI header (the "controlled" part).
- [ ] **Agent event audit trail**: persist each turn's full AgentEvent
      stream (Redis, TTL) → `GET /api/sessions/{id}/turns` for replay/debug.

### Tier 2 — Real tracing, still no accounts

- [ ] **Local trace UI via Jaeger**: add a compose `observability` profile
      with Jaeger all-in-one; new `ASSISTANT_OTLP_ENDPOINT` setting exports
      OTel spans there → full trace waterfalls at `localhost:16686`, zero
      signups.
- [ ] **Manual spans on the seams auto-instrumentation misses**: span per
      *agent turn*, per *LLM step*, per *tool execution* (attrs: tool,
      args size, result size), per *retrieval* (attrs: mode, candidates,
      scores) — this is what makes traces explain the agent, not just HTTP.
- [ ] **Token/usage capture**: request usage from OpenAI-compatible streams
      (`stream_options.include_usage`), attach to spans + turn summary;
      estimated (chars/4) for the fake provider.
- [ ] **Prompt/response debug toggle**: `ASSISTANT_LOG_PROMPTS=true` dumps
      full prompts + raw model output (dev-only; privacy note in config).

### Tier 3 — Metrics & dashboards

- [ ] **/metrics (Prometheus)**: counters (turns by backend, tool calls by
      tool, errors by type) + histograms (turn/LLM/tool/retrieval latency).
- [ ] **Grafana** in the observability profile with one pre-provisioned
      dashboard (traffic, latency, error rate, tool usage).
- [ ] **Cost accounting**: tokens → $ per session/day from captured usage.

### Tier 4 — Product-level visibility

- [ ] **"Explain this turn" panel in the UI**: render the audit trail as a
      timeline (step, duration, tool, result preview) next to the answer.
- [ ] **Eval trend history**: append eval runs to `evals/history.jsonl`
      with timestamp + config; print deltas — regressions become visible.
- [ ] **Cloud backends when tokens exist**: Logfire + Langfuse are already
      wired (`observability.py`) — add tokens, verify dashboards, done.

---

## Backlog — features

- [ ] **Your side (.env, minutes each)**: Groq key → real model in the demo;
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
