# 09 — Testing, operations & troubleshooting

**What this chapter covers: the automated test suite and what each file
proves, the day-to-day UI controls, how to run and operate the platform,
the failures an operator will actually hit, and how to extend it without
breaking the pattern.** It is not the manual click-through checklist — that
tiered, feature-by-feature script is
[reference/testing.md](../reference/testing.md); this chapter is what runs
without a human at the keyboard, plus how to run the platform once it does.

## 1. The automated suite (573 tests, fully offline)

```sh
uv run pytest -q          # ~26s. No network, no Docker, no keys.
uv run pytest -q -n 4     # ~16s, in parallel (pytest-xdist)
```

`-n 4` rather than `-n auto`: measured on this suite, `auto` spawned twelve
workers and came out **slower than serial** (30s vs 26s), because each worker
pays interpreter and fixture startup for a suite that only lasts half a minute.
Four was the sweet spot locally; CI uses `auto` because its runners have four
cores. It is deliberately not the default — interleaved output makes a failing
test harder to read.

Determinism comes from swappable fakes: `FakeLLM` (scripted heuristics),
`fakeredis`, in-memory Qdrant, `HermeticSettings` (ignores your `.env`), and
scripted provider errors. Map of the suite:

| File | Proves |
|---|---|
| test_ws.py | WS protocol end-to-end **×3 backends**: streaming, history resume, tool loop, backend switch, bounded memory, zero-infra mode |
| test_observability.py | `turn` frame, /metrics series, deep health (ok + degraded), audit trail timeline, InstrumentedLLM usage capture |
| test_llm_errors.py | error mapping (429/401/404/5xx/network), 429 backoff, `stream_options` fallback, `tool_use_failed` retry + `failed_generation` salvage, leaked-tool-syntax parsing, cost table |
| test_fetch_url.py | fetch_url (mocked httpx: GitHub repo/account/HTML/SSRF guard), relevance gate, duplicate-call guard, FakeLLM URL routing |
| test_agent.py / test_tool_loop.py | the custom loop's mechanics |
| test_pydantic_backend.py / test_langgraph_backend.py | runtime parity |
| test_rag.py | chunking, hybrid search, rerank, ingest idempotency |
| test_mcp.py | real stdio MCP servers spawn + tools round-trip |
| test_memory.py | rolling summarization math |
| test_api_routes.py / test_config.py | REST + auth + settings |
| test_documents_api.py | documents added at runtime: upload (file + pasted), list, re-upload replaces rather than duplicates, delete, rejected types, auth, and the empty-knowledge-base message |
| test_fake_parity.py | all three backends route the same prompt to the same tool — the regression guard for the drift that made one backend's offline fake miss a tool |
| test_rate_limit.py | the sliding-window limiter: the limit binds, buckets and callers are isolated, a refusal does not extend your own window, reads are never throttled, and both surfaces (WS turns, indexing writes) enforce it |
| test_sessions_api.py | the conversations panel: recency order, previews, expired sessions never listed, delete removes history + audit + index, transcript restore, auth |
| test_eval_gate.py | the retrieval quality gate: the committed baseline is actually achieved by the pipeline, and a drop is reported while float noise is not |
| test_docs_links.py | the documentation itself: every relative link resolves, no stray prose outside `docs/`, the index covers every folder |
| test_docs_consistency.py | numbers quoted in many documents at once (backend line counts, golden-set size, retrieval scores, suite size) agree with the code and with each other |
| test_docs_coverage.py | every setting, endpoint, metric, wire frame, tool, source file, dependency and run command is mentioned somewhere — so shipping a feature without documenting it fails the build |
| test_docs_standard.py | the documentation standard's mechanical half (numbered sections, bold scope, reasoned Related links, troubleshooting shape, dated numbers, labelled and used images) on the pages that have adopted it — a ratchet that only grows |
| test_ragas_harness.py | the LLM-judged eval's plumbing, tested without an LLM: the dataset matches Ragas' field contract, and unanswerable questions are dropped before a judge could score an honest "I don't know" as a hallucination |
| test_review_regressions.py | the defects a full review found, each reproduced before it was fixed: re-upload leaving orphan chunks, `?limit=0` inverting the cap, a hallucinated tool name becoming a metric label, an SSRF guard walked past by a redirect, a failed turn never reporting its cost |

Two slices of that map, measured just now (2026-09-05), to make "offline and
fast" concrete rather than asserted:

```sh
uv run pytest tests/test_rate_limit.py -q -p no:cacheprovider   # 11 passed in 0.48s
uv run pytest tests/test_mcp.py -q -p no:cacheprovider          # 5 passed in 3.72s
```

The second file is the one marked `slow` in the map above, and the 3.72 s is
mostly real Python subprocesses actually starting and shaking hands over
stdio — `test_stdio_servers_expose_namespaced_tools_and_execute` spawns both
bundled MCP servers for real and asserts `code__search_code` finds
`"custom.py"` when it greps this very repository for `class CustomAgent`.

Quality gates (CI, every push): `ruff check` · `ruff format --check` ·
`pyright` (0 errors) · `pytest` with a coverage floor — on Python 3.12 **and**
3.13 — plus a frontend job (typecheck, vitest, build), a Docker image build,
and a **retrieval quality gate**. A second workflow runs CodeQL, `pip-audit`
and `npm audit` weekly.

The quality gate is the one that catches what no assertion can. A chunking
tweak or a change to the fusion weights can leave every test green and still
make answers worse, so CI re-runs the 18-question golden set (in-process
Qdrant, no services) and fails if any metric falls below
[evals/baseline.json](../../evals/baseline.json):

```sh
uv run python evals/run_retrieval.py --memory --check     # what CI runs
uv run python evals/run_retrieval.py --memory --record    # append to history.jsonl
uv run python evals/run_retrieval.py --trend              # the recorded trend
```

That gate covers **retrieval**. Generation quality — whether the model
actually grounded its answer in what was retrieved — is measured separately
and deliberately outside CI, because every one of its metrics is an LLM call:

```sh
# once: the judge gets a Python 3.13 environment of its own (ragas has no 3.14 wheels)
UV_PROJECT_ENVIRONMENT=.venv-evals uv sync --python 3.13 --group evals
# groundedness judged by an LLM, gated by a floor, with a negative control that proves the judge
UV_PROJECT_ENVIRONMENT=.venv-evals uv run python -m evals.run_ragas --check --control
```

Both metrics families, and why only one of them can be a build gate, are
explained in [reference/metrics.md](../reference/metrics.md); the judge
itself — what Ragas is, how to run and read it, and the control that proves
it — is [reference/ragas.md](../reference/ragas.md).

Lowering a number in `baseline.json` is a deliberate act: do it in the same
commit as the change that caused it, and say in the message why the trade-off
is worth it.

**Manual testing** is scripted feature-by-feature in
[the testing checklist](../reference/testing.md) (tiers: zero-infra → Docker → real
model → observability).

## 2. Day-to-day controls in the UI

| Control | What it does |
|---|---|
| **Stop** (or `Esc`) | interrupts the answer in flight; keeps what streamed, still records the partial cost |
| **Chats** | recent conversations: reopen one (transcript is restored) or delete it |
| **Documents** | add/remove what the assistant can search, at runtime |
| **Standard / Dev** | hide or show tool cards and the per-turn stats line |
| backend selector | switch agent runtime; the session carries over |

## 3. Operating it

| Task | Command |
|---|---|
| API server (dev) | `uv run uvicorn assistant.main:app --reload` |
| Ingest a folder (CLI) | `uv run python -m assistant.rag.ingest <folder> [--recreate]` |
| Retrieval quality | `uv run python evals/run_retrieval.py --memory` |
| Everything in containers | `docker compose --profile app up --build` |

**Auth mode**: set `ASSISTANT_AUTH_TOKEN=<secret>` → `POST`/`DELETE
/api/documents`, both `/api/sessions/{id}/turns[...]` routes and
`/api/documents` need `Authorization: Bearer`, and the WS needs `?token=`.
Deliberately open: `GET /api/documents` (the panel lists before you
authenticate), open the UI once as `/?token=<secret>` (persisted). `/api/info`,
`/api/health`, `/healthz`, `/metrics` stay open by design. Production path:
replace with OIDC at a gateway.

**Secrets hygiene**: `.env` is gitignored; keys never live in code; `SecretStr`
keeps them out of logs. The `log_prompts` toggle is dev-only by policy
(conversations end up in logs).

### Where these controls live

| File | Role |
|---|---|
| [config.py](../../src/assistant/config.py) | every setting named on this page (`rate_limit_*`, `auth_token`, `log_*`, …), validated once at startup |
| [api/rate_limit.py](../../src/assistant/api/rate_limit.py) | the sliding-window limiter behind both `ASSISTANT_RATE_LIMIT_*` settings |
| [api/routes.py](../../src/assistant/api/routes.py) | `require_token` (auth), `limit_writes` (the upload bucket), and the open `/api/info` / `/api/health` endpoints |

The two rate-limit buckets are independent and both keyed by caller, not
global: `ASSISTANT_RATE_LIMIT_TURNS_PER_MINUTE` (default 20, per session) on
chat turns, `ASSISTANT_RATE_LIMIT_UPLOADS_PER_HOUR` (default 50, per bearer
token or peer address) on `POST /api/documents`. Both live in one sliding
Redis sorted set per caller, checked *before* any LLM call or embedding
work, and a refusal is one HTTP 429 or WS `error` frame — never a hang.
Setting either limit to `0` disables just that bucket;
`ASSISTANT_RATE_LIMIT_ENABLED=false` disables both at once.

## 4. Troubleshooting (the failures you'll actually see)

| Symptom | Cause | Fix |
|---|---|---|
| Health dot **amber**, hover says `qdrant: error`; docs answers apologize | Qdrant container down / collection missing | `docker compose up -d`; re-ingest if the collection is empty |
| Chat: *"LLM rate limit hit (429). Provider says: … tokens per day (TPD): Limit 100000 …"* | OpenAI **daily** budget for the model is spent (a long session can do it) | switch `ASSISTANT_LLM_MODEL=gpt-4o-mini` (own budget) or wait for reset / paid tier |
| Brief stalls mid-turn, logs show `LLM rate limited (429) — retry …` | per-minute limit; automatic backoff riding it out | nothing — working as designed |
| Chat: *"rate limit reached — too many messages. Try again in Ns (or raise ASSISTANT_RATE_LIMIT_*)."* | this session hit `ASSISTANT_RATE_LIMIT_TURNS_PER_MINUTE` (default 20/min) | wait `N` seconds, or raise the limit; the socket stays open the whole time |
| `POST /api/documents` returns 429 with a `Retry-After` header and *"rate limit reached — too many indexing requests…"* | this caller hit `ASSISTANT_RATE_LIMIT_UPLOADS_PER_HOUR` (default 50/hour) | wait, raise the limit, or disable it (`=0`) for bulk imports — `DELETE` is never throttled, so cleanup always works |
| Chat: *"model failed to generate a valid tool call"* (rare) | llama emitted malformed tool JSON 3× and `failed_generation` was unparseable | resend / rephrase; already auto-retried + salvage-attempted |
| Answer contains raw `<function…>` text | should **never** happen now (salvage layer) — if seen, it's a new llama syntax variant | add it to `_LEAKED_CALL_PREFIX` in [llm/client.py](../../src/assistant/llm/client.py) + a test |
| *"duplicate call — … use the result you already received"* in a tool card | model repeated an identical call; guard answered | cosmetic; the model continues with the earlier result |
| *"No relevant chunks matched this exact wording …"* | relevance gate: the query shares no meaningful token with any chunk | expected for off-topic questions — the honest answer |
| `search_docs` returns chunks from a repo you tested with | you ingested extra sources into `docs` | `uv run python -m assistant.rag.ingest evals/corpus --recreate` |
| Chat: *"LLM authentication failed"* / *"Model not available"* | bad key / model name typo | fix `ASSISTANT_LLM_API_KEY` / `ASSISTANT_LLM_MODEL`, restart |
| UI loads but "disconnected" | server down, or auth on and no `?token=` | start server / open `/?token=<secret>` |
| No traces in Jaeger | `ASSISTANT_OTLP_ENDPOINT` unset, or stack not up | set it + restart server; check :16686 is reachable |
| Prometheus target down at `api:8000` | you run the server on the host | expected — `host.docker.internal:8000` is the live target |
| `fetch_url` shows GitHub page chrome instead of clean README | unauthenticated GitHub API rate limit (60 req/h) → HTML fallback | wait an hour, or accept — the model still reads it |
| JSON logs show `"exc_info": true` but no traceback | you're on a build older than the `format_exc_info` fix | pull latest |

## 5. Extending safely (the pattern)

Any new capability should land with: a config default that works offline →
telemetry at the seam (span + metric + log) → a deterministic test with a
fake → a line in the docs. That pattern is why the platform stays green: the
suite catches contract breaks, and live quirks get reproduced as scripted
fakes the same day they're discovered (see test_llm_errors.py's history).

## 6. Showing it live

Three short demos, all offline except the third:

1. Lower the chat limit for the demo
   (`ASSISTANT_RATE_LIMIT_TURNS_PER_MINUTE=2 uv run uvicorn assistant.main:app`)
   and send three messages fast — *"the third gets an error frame, not a
   stall, and the socket is still open — send a fourth a few seconds later
   and it goes through."* A few seconds, no cost.
2. Set `ASSISTANT_AUTH_TOKEN=s3cret`, restart, and load `/` with no token —
   *"the health dot still turns green — `/api/health` is deliberately
   open — but Chats and Documents stay empty until I open
   `/?token=s3cret` once."* Under ten seconds.
3. Run `uv run python evals/run_retrieval.py --memory --check` — *"eighteen
   golden questions, an in-memory Qdrant, no network — this is the exact
   command CI runs on every push, and it takes about two seconds."* ~2 s,
   nothing (see [reference/metrics.md](../reference/metrics.md) for what the
   output means line by line).

## 7. Reading it honestly

- **Rate limiting is a budget guard, not access control.** It is keyed by
  session id (chat) or bearer token / peer address (uploads); a client that
  simply reconnects with a new session id gets a fresh bucket. It exists to
  stop one stuck client from draining a day's LLM quota by accident, not to
  resist a deliberate abuser — [api/rate_limit.py](../../src/assistant/api/rate_limit.py)
  says so in its own module docstring.
- **The `-n 4` sweet spot is one measurement on one machine.** `auto`
  spawning twelve workers and losing to serial execution was true here, on
  this suite, at this size; a slower or faster CPU, or a much larger suite,
  could tip the balance back the other way. Re-measure before trusting it
  elsewhere.
- **Auth is one shared secret, not identity.** Every caller with the token
  has the same access as every other; there is no per-user audit trail, only
  per-session. [future-tools.md §4](../project/future-tools.md) names OIDC as
  the deferred upgrade, gated on a second team ever using this instance.
- **The retrieval gate and the judged metric can disagree, and only one
  gates CI.** A change could hold `recall@1` steady while quietly making the
  model's *answers* less faithful (or the reverse) — the two are measured
  separately in `evals/`, and only the free, deterministic one runs on every
  push (§1).
- **"573 tests" is a snapshot, not a promise.** New tests land between
  updates to this number; `tests/test_docs_consistency.py` tolerates drift
  up to 5% before failing the build, which is a deliberate looseness, not
  proof the count is current at this exact moment.

## 8. Related

- [reference/testing.md](../reference/testing.md) — the manual, tiered checklist this chapter's automated suite complements
- [reference/security.md](../reference/security.md) — the rate limiter and auth token as security controls, with the attacks they were tested against
- [handbook/07 — Observability](07-observability.md) — how to watch the platform this chapter operates, turn by turn
- [reference/metrics.md](../reference/metrics.md) — what the retrieval quality gate measures and why only it can be a CI gate
- [reference/ragas.md](../reference/ragas.md) — the judged metric that sits outside CI, and the control that proves the judge works
- [project/documentation-standard.md](../project/documentation-standard.md) — the rules `test_docs_standard.py` (in the map above) enforces
