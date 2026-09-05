# 04 — LLM: providers, models, tokens, cost, and surviving real providers

**What this chapter covers: every provider and model this assistant can run
against, exactly how one streamed LLM step survives a real provider's
quirks, how tokens and cost are counted and priced, and what actually spends
money in this repository.** It does not cover how a tool call is chosen or
executed once the model asks for one — see
[06 — Tools & MCP](06-tools-mcp.md) for that; this page stops at the model
boundary.

## 1. Providers and models

The provider is **config, not code** — every hosted option speaks the
OpenAI-compatible chat API through one client
([llm/client.py](../../src/assistant/llm/client.py)):

| `ASSISTANT_LLM_PROVIDER` | Endpoint | Typical models | Key |
|---|---|---|---|
| `fake` *(default)* | — offline, deterministic | — | none |
| `openai` | SDK default | `gpt-4.1-nano` *(default — cheapest that still calls tools)*, `gpt-4o-mini`, `gpt-4.1-mini`, `gpt-4o` | paid, platform.openai.com |
| `ollama` | localhost:11434/v1 | anything pulled locally | none |
| `gemini` | Google's OpenAI-compat endpoint | gemini models | free tier exists |

The dict behind the table is short enough to read whole:
`PROVIDER_BASE_URLS = {"openai": None, "ollama": "http://localhost:11434/v1",
"gemini": "https://generativelanguage.googleapis.com/v1beta/openai/"}`
([llm/client.py](../../src/assistant/llm/client.py)) — `None` means "let the
SDK use its own default," which is exactly what makes adding a fourth
OpenAI-compatible provider a one-line change.

**The two modes you will actually use.** `fake` for everything you can test
without spending — the full tool loop, RAG, streaming, cancellation, all 573
tests — and `openai` when you want real answers. Switching is two lines in
`.env`; no code changes, because the provider is a config value.

**FakeLLM** is the dev/test default: echoes deterministically and plays a
one-round agent on keywords (PRs → github tool, `search code for X` → code
tool, any URL → `fetch_url`, trailing `?` → `search_docs`), so the whole
loop demos offline at zero cost.

## 2. How one LLM step works (the streaming client)

`OpenAICompatibleLLM.stream_step(messages, tools)` is an async generator that
yields three event types the agent loops consume:

- `TextDelta` — a streamed piece of the answer (forwarded to the UI live),
- `ToolCallRequest` — accumulated from interleaved stream fragments, emitted
  at stream end,
- `UsageEvent` — real token counts from the provider's final chunk
  (requested via `stream_options: {include_usage: true}`; consumed by
  telemetry, never seen by agent loops).

Around that core sit four hardening layers, every one of them earned from a
live failure rather than anticipated:

1. **`stream_options` reject-retry** — some OpenAI-compatible providers 400
   on `stream_options`; the client retries once without it. Pinned by
   `test_create_stream_drops_stream_options_on_bad_request` in
   [tests/test_llm_errors.py](../../tests/test_llm_errors.py).
2. **429 backoff** — beyond the SDK's quick built-in retries, up to 2 extra
   attempts honoring the `Retry-After` header (capped 15 s/wait). Provider
   rate-limit windows are per-minute; quick retries alone don't outlast them. Four
   tests in the same file cover it, including the edge case that gives this
   line its name in [CLAUDE.md](../../CLAUDE.md): `retry-after: 0` is valid
   and means "retry now," so the code tests the header against `None`, never
   truthiness (`test_create_stream_retries_rate_limits_with_retry_after`).
3. **`tool_use_failed` recovery** — a provider can abort a 200 stream with
   this code when the model emits malformed tool-call JSON. The step is
   retried up to 2×, and as a last resort the call is **salvaged from the
   provider's `failed_generation` payload** and executed anyway
   (`test_stream_step_retries_tool_use_failure`,
   `test_stream_step_recovers_call_from_failed_generation`).
4. **Leaked-tool-syntax salvage** — a model sometimes prints its native tool
   markup as *text*: `<function.name>{…}</function>`, `<function=name>{…}`,
   `<function(name){…}`. One observed variant that shaped the matching regex:
   `(function=name>{...}` — an opening parenthesis instead of the angle
   bracket, seen live on llama-3.1-8b
   ([llm/client.py](../../src/assistant/llm/client.py)); missing it would
   have put raw markup in front of a user, which is the whole failure this
   exists to prevent. The client holds back text that starts like this and
   parses it into real `ToolCallRequest`s (`parse_leaked_tool_calls`,
   brace-matched JSON so nested arguments work); if it turns out to be prose,
   it's flushed as text. The chat never sees raw markup.

Every layer above is pinned offline: the retry and salvage behaviour in
[tests/test_llm_errors.py](../../tests/test_llm_errors.py), and the leaked-
markup edge cases in
[tests/test_review_regressions.py](../../tests/test_review_regressions.py)
— including the negative case that proves the guard is not trigger-happy,
`test_a_sentence_starting_like_the_markup_still_streams`, for an ordinary
answer that merely *starts* with text resembling `(function`.

If all of that fails, the WS layer maps the exception to a clear error frame
(`describe_llm_error` in [llm/errors.py](../../src/assistant/llm/errors.py)) by walking the
exception chain and duck-typing `status_code` — so it works for openai *and*
pydantic-ai errors:

| Failure | Chat message says | Metric `errors_total{kind}` |
|---|---|---|
| 429 | the provider's own text (which limit + how long to wait) | `rate_limited` |
| 401/403 | check `ASSISTANT_LLM_API_KEY` | `auth_failed` |
| 404 | check `ASSISTANT_LLM_MODEL` + provider detail | `model_unavailable` |
| 5xx | provider error, try again | `provider_error` |
| network | check network / `ASSISTANT_LLM_BASE_URL` | `provider_unreachable` |
| tool_use exhausted | "model failed to generate a valid tool call — resend" | `tool_use_failed` |

## 3. Tokens: real vs estimated

Every turn's stats line shows `prompt→completion tok`. The source:

- **Real** — the provider reported usage (`UsageEvent`); shown without a
  suffix. OpenAI reports usage on streams when asked for it.
- **Estimated** — `(est)` suffix: `chars // 4` fallback, used for FakeLLM and
  for providers that don't report usage. The Pydantic AI backend runs its own
  model layer, so the wrapper cannot see its calls; since 2026-09-04 it
  reports the run's usage into the same turn stats itself, and only its
  offline `FunctionModel` fake marks counts as estimates.

`InstrumentedLLM` ([telemetry.py](../../src/assistant/telemetry.py)) accumulates
per-turn totals in a `ContextVar` (`TurnStats`): steps, LLM ms, tokens, and
whether anything was estimated. Those feed the `turn` WS frame, the
`turn.summary` log line, Prometheus (`assistant_tokens_total{direction}`),
and the audit record.

A real measurement, 2026-09-04, turn `b099e9cd40ff`
(*"How is todometer released?"*, `gpt-4.1-nano`): **8,380** prompt tokens and
**175** completion tokens, real (no `(est)` suffix) because OpenAI reported
usage on the stream — [reference/tools.md §2](../reference/tools.md) has the
full turn. The same shape — real counts, no estimate — held on all three
backends when the identical question was asked of each on 2026-09-05, prompt
sizes within 50 tokens of each other
([backend-comparison.md §5](../reference/backend-comparison.md)).

## 4. Cost accounting

`estimate_cost_usd(model, prompt_tokens, completion_tokens)` prices tokens
against a small table (per 1M tokens, listed pay-as-you-go prices):

| Model | prompt $ | completion $ |
|---|---:|---:|
| gpt-4.1-nano *(default)* | 0.10 | 0.40 |
| gpt-4o-mini | 0.15 | 0.60 |
| gpt-4.1-mini | 0.40 | 1.60 |
| gpt-4o | 2.50 | 10.00 |
| *(fake / unknown)* | 0 | 0 |

Continuing the same worked turn from §3: 8,380 prompt tokens × $0.10/1M +
175 completion tokens × $0.40/1M = **$0.000908** — exactly the `cost_usd`
the stats line and the audit record showed for turn `b099e9cd40ff`.

The number is **indicative** — list prices, not your invoice, and 0 for the
fake provider by definition. Surfaces: `~$0.0026` in the
stats line (hidden when 0), `cost_usd` in the turn frame + audit record +
`turn.summary` log, and the `assistant_cost_usd_total{model}` counter
(per-day spend = `increase(assistant_cost_usd_total[1d])` in Prometheus).

## 5. Rate limits and budget (measured, not theoretical)

Two kinds of limit, and they produce identical 429s with different advice:

- **Per-minute** (requests + tokens): brief bursts hit it; the client's
  backoff usually rides it out invisibly.
- **Account balance / project budget**: no amount of waiting helps.

Measured on `gpt-4.1-nano` with a service-account key, from the response
headers:

```
x-ratelimit-limit-requests: 5000        (per minute)
x-ratelimit-limit-tokens:   4000000     (per minute)
```

At those limits, per-minute throttling is effectively unreachable for a demo
— which is the practical difference from a free tier, where the schemas of a
tool-heavy turn alone could exceed the allowance.

**What it actually costs.** A tool-using turn is 3–9k prompt tokens,
because each LLM step re-sends the conversation plus the tool result — a
2,000-character search result on an ingested repository is most of the
upper end. On `gpt-4.1-nano` that is **$0.0004–0.001 a turn** (the measured
turn `b099e9cd40ff` cost $0.000908) — a full workshop demo costs a few
cents, and the complete eval suite (18 questions judged by Ragas) under five.

**Checking the balance.** You cannot, with an application key: OpenAI's
`/v1/organization/costs` returns 403 `Missing scopes: api.usage.read`. That is
correct design — a key embedded in an app should not read the org's finances.
Use platform.openai.com → Usage, or ask an admin to grant that scope to the
service account.

What you *can* do without leaving the app: every turn records its own
`cost_usd` from real provider-reported token counts, accumulating in
`assistant_cost_usd_total{model}`:

```sh
curl -s localhost:8000/metrics | grep assistant_cost_usd_total
```

Practical playbook:
- Watch spend live: that counter, the `assistant_tokens_total` rate in
  Grafana, or the per-turn stats line in Dev mode.
- Need cheaper or stronger? `ASSISTANT_LLM_MODEL` is one line — `gpt-4.1-nano`
  is the floor that still calls tools reliably; `gpt-4o-mini` and
  `gpt-4.1-mini` buy capability.
- Need *free*? `ASSISTANT_LLM_PROVIDER=fake` runs the whole loop offline, and
  `ollama` runs a real model locally at no per-token cost.
- The **duplicate-call guard** (chapter 06) exists exactly because repeated
  identical tool calls were the fastest way to burn budget.

## 6. What actually costs money

Only two things in this repository ever call a paid API, and neither is
automated. Everything else — the whole test suite, every CI job, all four
lint/type/format gates, and the retrieval quality gate — is free by
construction.

| Action | Calls a paid API? | Notes |
|---|---|---|
| `uv run pytest` (all tests) | **No** | Verified: the suite passes with the provider URL pointed at a dead port |
| Every CI job | **No** | The repository has **zero** Actions secrets — CI has no key to spend |
| `evals/run_retrieval.py` | **No** | Uses the offline `hash-512` embedder and no LLM at all |
| `ASSISTANT_LLM_PROVIDER=fake` | **No** | The whole tool loop runs offline |
| **Chatting with `provider=openai`** | **Yes** | $0.0004–0.001 a turn on `gpt-4.1-nano`, depending on tool-result size |
| **`evals/run_ragas.py`** | **Yes** | Several LLM calls per question — the expensive one |
| `evals/compare_embeddings.py` | Only if `ASSISTANT_EMBEDDING_API_KEY` is set | ~$0.0002 for the whole corpus |

**How the test suite is proven free.** Tests build `Settings` through
`HermeticSettings`, which sets `env_file=None` so your `.env` is never read,
and inject `FakeLLM` or a scripted stub. To confirm rather than assume, run
the suite with the endpoint made unreachable — anything making a real call
fails instantly:

```sh
ASSISTANT_LLM_BASE_URL=http://127.0.0.1:9 uv run pytest -q
```

PowerShell: `$env:ASSISTANT_LLM_BASE_URL = "http://127.0.0.1:9"` once per
shell, then `uv run pytest -q`.

Re-verified 2026-09-05 (`-p no:cacheprovider`, `ASSISTANT_LLM_BASE_URL`
pointed at `127.0.0.1:9`): every test that exercises the LLM boundary still
passes — nothing hangs or errors trying to reach the network for a real
completion. (One unrelated test outside this chapter's scope — a
documentation suite-size consistency check — fails independent of this
setting; chapter 09 is where that lives.)

**A key being present costs nothing.** `ASSISTANT_EMBEDDING_API_KEY` only
bills when `ASSISTANT_EMBEDDING_PROVIDER=openai`; the default is `hash`, which
is offline. The same is true of the LLM key while `ASSISTANT_LLM_PROVIDER=fake`.

### The setting that quietly multiplies your bill

`ASSISTANT_MCP_SERVERS` decides how many tool schemas ride in **every** prompt,
and tool schemas are charged as input tokens on every single turn:

| Configuration | Tools | Schema per prompt | Same question costs |
|---|---:|---:|---:|
| Bundled default (code + mocked GitHub) | 5 | ~530 tokens | **$0.00016** |
| Real GitHub MCP server | 44 | ~12,900 tokens | **$0.00186** |

That is **12× the price for an identical answer** — measured, same question,
same model. If you do swap in the real GitHub server, trim it with its
`--toolsets pull_requests,issues` flag rather than loading all 44.

### Keeping the bill near zero

1. **Develop on `fake`.** It exercises retrieval, the tool loop, streaming,
   cancellation and the audit trail. Switch to `openai` only to check answer
   *quality*.
2. **Watch your own counter** rather than the OpenAI dashboard — it updates
   per turn: `curl -s localhost:8000/metrics | grep assistant_cost_usd_total`
3. **Keep `gpt-4.1-nano`** unless an answer is visibly too weak.
4. **Use `--limit` on Ragas.** The full 18-question run is ~200 LLM calls,
   and `--control` judges everything twice.
5. **Leave `ASSISTANT_MCP_SERVERS` unset** unless you need the real server.

## 7. Switching models — checklist

1. Edit `.env`: `ASSISTANT_LLM_MODEL=...` (and provider/key if changing
   provider). 2. Restart the server. 3. Send one message; check the stats
line: real tokens (no "(est)") confirm usage reporting works; the model name
shows in `/api/health` under `llm`. 4. If the model was a typo you'll get the
`model_unavailable` chat error naming it.

## 8. Showing it live

About ten seconds, one restart:

1. With Mode A running, send a message and note the stats line: tokens end
   in `(est)`.
2. Edit `.env` — set `ASSISTANT_LLM_PROVIDER=openai` and a real key, restart
   the server ([02 — Getting started §2](02-getting-started.md), Mode C).
   *"Same UI, same question —"*
3. Send the same message — *"— and the `(est)` suffix is gone: that's a real
   provider reporting real usage, not a bigger number."*
4. `curl -s localhost:8000/metrics | grep assistant_cost_usd_total` — *"and
   the counter it just added to."*

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `LLM rate limit hit (429). Provider says: …` in the chat | provider per-minute limit hit, retries exhausted | wait for the backoff, or switch `ASSISTANT_LLM_MODEL` to a model with separate quota |
| `LLM authentication failed — check ASSISTANT_LLM_API_KEY.` | missing or wrong key | set `ASSISTANT_LLM_API_KEY` |
| `Model not available — check ASSISTANT_LLM_MODEL. Provider says: …` | typo'd or retired model name | fix `ASSISTANT_LLM_MODEL`; `/api/health` echoes the model currently in use |
| `ValueError: ASSISTANT_LLM_API_KEY is required for provider 'openai'` at startup | switched provider without setting a key | set the key, or switch back to `ASSISTANT_LLM_PROVIDER=fake` |
| `403 Missing scopes: api.usage.read` from `/v1/organization/costs` | an application key cannot read org-level billing by design | use platform.openai.com → Usage, or grant the scope to the service account |
| stats line stuck on `(est)` tokens against a real provider | the `pydantic_ai` backend's own model layer, or a provider that just doesn't report usage | expected on `pydantic_ai` before the 2026-09-04 fix; usage is folded into the same counters now ([backend-comparison.md §6](../reference/backend-comparison.md)) |

## 10. Reading it honestly

- **Cost is indicative, frozen at a point in time.** The price table lives in
  [telemetry.py](../../src/assistant/telemetry.py) as a plain dict; a
  provider price change or a promotional rate makes every number here wrong
  until someone edits `MODEL_PRICES_PER_MTOK` — nothing checks it against the
  provider automatically.
- **`chars // 4` is not a tokenizer.** The estimate used for the fake
  provider and the pydantic-ai fallback path can be meaningfully off,
  especially for code-heavy or non-English text — treat `(est)` numbers as
  a rough order of magnitude, not a bill.
- **The dead-port proof (§6) shows no *LLM* call happens — not that no
  network call of any kind happens.** Mode B/production still reaches
  Qdrant, Redis and the GitHub API; this chapter's guarantee is scoped to
  the model boundary.
- **The retry budget is a deliberate ceiling, not a guarantee.** 2 rate-limit
  retries and 2 `tool_use_failed` retries mean a provider having a bad minute
  still surfaces as an error to the user, by design — more retries would
  trade a clear failure for a much longer hang.

## 11. Related

- [02 — Getting started](02-getting-started.md) — Mode C, and every `.env` variable this chapter's numbers assume
- [06 — Tools & MCP](06-tools-mcp.md) — what happens after the model asks for a tool
- [reference/tools.md](../reference/tools.md) — the real turn this chapter's worked numbers come from
- [reference/backend-comparison.md](../reference/backend-comparison.md) — the same question, same cost, on all three backends
- [project/tech-stack.md](../project/tech-stack.md) — why OpenAI and `gpt-4.1-nano` over Anthropic, Groq or OpenRouter
- [theory/01 — LLM basics](../theory/01-llm-basics.md) — the concepts this chapter assumes
