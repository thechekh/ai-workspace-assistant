# Manual testing checklist

Feature-by-feature "what to do → what you should see". Work top to bottom:
each tier adds infrastructure; everything in a tier keeps working in the next
one. (`uv run pytest -q` covers the automated side — this file is for
clicking through the real thing.)

## Tier A — zero infra (no Docker, no key)

Setup: in `.env` set `ASSISTANT_REDIS_URL=fakeredis://` and
`ASSISTANT_LLM_PROVIDER=fake` (or just no `.env` at all), then:

```sh
uv run uvicorn assistant.main:app --port 8000
# open http://localhost:8000/
```

- [ ] **Streaming chat**: send `hello` → tokens stream in word by word, then
      the line solidifies. The reply is the fake echo and reports
      `(2 messages in context)`.
- [ ] **Standard mode is clean**: with the header toggle showing
      **Standard**, a tool-using question renders no tool card and no stats
      line — just a brief "working…" hint, then the answer.
- [ ] **Dev mode reveals everything, retroactively**: click the toggle →
      **Dev**. The tool cards and stats lines appear on messages that were
      *already* on screen (nothing is re-sent). Reload the page — the mode
      persists.
- [ ] **Stats line** (Dev mode): under the answer: duration, `first token N ms`,
      `1 LLM step`, `N→M tok (est)`. No `$` figure (fake provider is free).
- [ ] **Details timeline**: click `details` → for a plain echo just a
      `final` row; after a tool turn (below) also `tool_call`/`tool_result`
      rows with `+ms` offsets.
- [ ] **Knowledge base starts empty**: open the **Documents** panel in the
      header → "Nothing indexed yet". Ask `Which service generates PDF
      invoices?` → the assistant says the knowledge base is empty and asks you
      to upload documents (it does *not* invent an answer).
- [ ] **Add a document in flight**: drop
      `evals/corpus/architecture/services.md` on the panel's dropzone (or
      click to pick it) → toast `Indexed N chunks from 1 document(s)`, and the
      file appears in the list with its chunk count.
- [ ] **It becomes searchable immediately**: ask the *same* question again →
      `search_docs` card with real chunks, answer cites
      `architecture/services.md`. This is the strongest single demo.
- [ ] **Paste instead of upload**: expand "or paste text", name it
      `runbook.md`, paste a heading + a line, **Add** → appears in the list.
- [ ] **Re-upload replaces**: drop the same file again → chunk count stays
      the same (ids are deterministic), it does not double.
- [ ] **Remove**: click ✕ on a document → toast, it leaves the list, and
      asking about it again returns "no relevant documents".
- [ ] **Rejected uploads explain themselves**: try a `.png` → 400 with
      `unsupported type` rather than a silent no-op.
- [ ] **Tools offline** (FakeLLM heuristics, see [tools.md](tools.md)):
      - `Show latest PRs` → tool card `github__list_pull_requests`, answer
        quotes `#142 …`
      - `search code for class CustomAgent` → tool card `code__search_code`
        with real hits from this repo
      - `Which service generates PDF invoices?` → `search_docs` card; with no
        Qdrant at all the result is an `error: tool 'search_docs' failed…` —
        **the turn must still answer** (graceful degradation)
- [ ] **fetch_url** *(needs internet)*: paste
      `what is https://github.com/thechekh/awsomequiz-streamlit about?` →
      tool card `fetch_url`, answer grounded in the repo's real README;
      `https://github.com/thechekh` alone → your public repo list.
- [ ] **Off-topic honesty**: ask about something outside the docs (e.g.
      `what certificates does the quiz project offer?` without a URL) —
      `search_docs` returns "No relevant documents…" and the answer says the
      docs don't cover it. No invented facts, no repeated searches.
- [ ] **Health dot**: amber (degraded) — hover it: `redis: ok`,
      `qdrant: error`, `mcp: ok`. (`curl localhost:8000/api/health` shows the
      same as JSON.)
- [ ] **Backend switcher**: switch custom → pydantic_ai → langgraph, send a
      message on each — same behavior, stats line shows the new backend name
      in its tooltip; the session (history) survives the switch.
- [ ] **Session resume**: reload the page → same session id, history intact
      (fakeredis keeps it until the *server* restarts). "New session" clears.
- [ ] **Bad frame handling**: from devtools:
      `new WebSocket("ws://localhost:8000/chat")` + `ws.send("not json")` →
      error frame, socket stays usable.
- [ ] **/metrics**: `curl localhost:8000/metrics | grep assistant_` —
      `assistant_turns_total`, `assistant_tool_calls_total{status=...}`,
      token counters all present and growing.
- [ ] **Audit API**: `curl localhost:8000/api/sessions/<id>/turns` (id from
      the session — it's in the WS `session` frame / sessionStorage) → JSON
      with per-turn stats + event timelines.

## Tier B — real infra (Docker: Redis + Qdrant)

```sh
docker compose up -d               # redis + qdrant
# .env: remove/comment ASSISTANT_REDIS_URL (defaults to localhost:6379)
uv run python -m assistant.rag.ingest evals/corpus   # or click "Re-index"
uv run uvicorn assistant.main:app --port 8000
```

- [ ] **Health dot goes green**: all components `ok`; qdrant shows the
      `docs` collection with a points count.
- [ ] **Real RAG**: `Which service generates PDF invoices?` → `search_docs`
      returns actual chunks like `[architecture/services.md — billing-service]
      (score …)`; the answer cites the source file.
- [ ] **Re-index button** *(only with `ASSISTANT_CORPUS_DIR` set — without it the endpoint returns 400 by design, because uploaded documents need no re-indexing)*: toast `Re-index queued` (real Redis → taskiq path;
      run `uv run taskiq worker assistant.worker:broker` to process it) or
      `Re-indexed N chunks` inline when on fakeredis.
- [ ] **Sessions survive restarts**: restart uvicorn, reload page — history
      still there (Redis persistence).
- [ ] **Degradation drill**: `docker stop bench_project-qdrant-1` → dot turns
      amber within ~10 s, docs questions degrade gracefully, everything else
      works. `docker start …` → green again.

## Tier C — real model (Groq key)

```sh
# .env: ASSISTANT_LLM_PROVIDER=groq, ASSISTANT_LLM_API_KEY=gsk_...
#       ASSISTANT_LLM_MODEL=llama-3.3-70b-versatile
```

- [ ] **Real streaming**: visibly incremental tokens, first-token latency in
      the stats line is real network+inference time.
- [ ] **Real token usage**: stats line shows `N→M tok` **without** `(est)` —
      Groq reports usage via `stream_options.include_usage`.
- [ ] **Cost figure**: `~$0.000X` appears (indicative at listed per-token
      prices; Groq free tier actually bills $0). Also
      `assistant_cost_usd_total` in `/metrics`.
- [ ] **Tool choice by a real model**: `What's in PR 141 and should we merge
      it?` → model calls `github__get_pull_request(141)` on its own and
      reasons over the result.
- [ ] **llama tool-syntax salvage**: llama-3.3 sometimes emits its tool call
      as text (`<function...>`) or trips Groq's `tool_use_failed` error.
      Expected: the tool **still runs** — server logs show
      `retrying step (…/2)` / `recovered N tool call(s) from …`; the chat
      never shows raw `<function…>` markup. Only after repeated failure does
      a friendly "model failed to generate a valid tool call" error appear.
- [ ] **Rate-limit UX**: hammer 5–6 messages quickly. Groq free tier limits
      `llama-3.3-70b-versatile` per minute (≈30 req, ≈6k tokens) **and per
      day (100k tokens — a long testing session can exhaust it)**. Expected:
      short waits (client retries with backoff, `LLM rate limited (429) —
      retry…` in server logs); on exhaustion the chat shows Groq's own
      message (which limit + how long to wait), never a generic server
      error. `/metrics`: `assistant_errors_total{kind="rate_limited"}`.
      Daily budget gone? Set `ASSISTANT_LLM_MODEL=llama-3.1-8b-instant`
      (separate, larger daily budget) and keep testing.
- [ ] **Model-typo UX**: set `ASSISTANT_LLM_MODEL=does-not-exist`, restart,
      send a message → *"Model not available — check ASSISTANT_LLM_MODEL.
      Provider says: …"*; restore the real model after.

## Tier D — observability stack

```sh
docker compose --profile observability up -d   # Jaeger + Prometheus + Grafana
# .env: ASSISTANT_OTLP_ENDPOINT=http://localhost:4318 ; restart uvicorn
```

- [ ] **Jaeger** (http://localhost:16686): service
      `ai-workspace-assistant` → a docs-question trace shows the waterfall
      `agent.turn` → `llm.step` → `tool.execute` → `rag.retrieve`, with token
      counts and durations as span attributes.
- [ ] **Prometheus** (http://localhost:9090): target `assistant` is UP
      (host-run target; the compose one may show down — expected).
- [ ] **Grafana** (http://localhost:3000, no login): dashboard *AI Workspace
      Assistant* — send a few chat messages and watch turn rate, p50/p95
      latency, tokens/min, tool calls move on the 5 s refresh.
- [ ] **Structured logs**: run the server with `ASSISTANT_LOG_JSON=true` —
      every line is JSON carrying `session_id`/`turn_id`/`backend`; one
      `turn.summary` per message; `ASSISTANT_LOG_PROMPTS=true` additionally
      dumps prompts/completions (dev only).

## Auth mode (any tier)

```sh
# .env: ASSISTANT_AUTH_TOKEN=s3cret ; restart
```

- [ ] Open `http://localhost:8000/?token=s3cret` once → UI works (token is
      persisted); without it the WS closes (1008) and `/api/reindex`,
      `/api/sessions/{id}/turns` return 401. `/api/info`, `/api/health`,
      `/healthz`, `/metrics` stay open.
- [ ] `details` timeline still loads (the UI sends the bearer header).
