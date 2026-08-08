# 04 — LLM: providers, models, tokens, cost, and surviving real providers

## Providers and models

The provider is **config, not code** — every hosted option speaks the
OpenAI-compatible chat API through one client
([llm/client.py](../../src/assistant/llm/client.py)):

| `ASSISTANT_LLM_PROVIDER` | Endpoint | Typical models | Key |
|---|---|---|---|
| `fake` *(default)* | — offline, deterministic | — | none |
| `groq` | api.groq.com/openai/v1 | `llama-3.3-70b-versatile` (best quality), `llama-3.1-8b-instant` (fast, separate quota) | free at console.groq.com |
| `ollama` | localhost:11434/v1 | anything pulled locally | none |
| `openai` | SDK default | `gpt-4o-mini`, `gpt-4o`, `gpt-4.1-mini` | paid |
| `gemini` | Google's OpenAI-compat endpoint | gemini models | free tier exists |

**FakeLLM** is the dev/test default: echoes deterministically and plays a
one-round agent on keywords (PRs → github tool, `search code for X` → code
tool, any URL → `fetch_url`, trailing `?` → `search_docs`), so the whole
loop demos offline at zero cost.

## How one LLM step works (the streaming client)

`OpenAICompatibleLLM.stream_step(messages, tools)` is an async generator that
yields three event types the agent loops consume:

- `TextDelta` — a streamed piece of the answer (forwarded to the UI live),
- `ToolCallRequest` — accumulated from interleaved stream fragments, emitted
  at stream end,
- `UsageEvent` — real token counts from the provider's final chunk
  (requested via `stream_options: {include_usage: true}`; consumed by
  telemetry, never seen by agent loops).

Around that core sit four hardening layers, all earned from live testing
against Groq:

1. **`stream_options` reject-retry** — some OpenAI-compatible providers 400
   on `stream_options`; the client retries once without it.
2. **429 backoff** — beyond the SDK's quick built-in retries, up to 2 extra
   attempts honoring the `Retry-After` header (capped 15 s/wait). Free-tier
   windows are per-minute; quick retries alone don't outlast them.
3. **`tool_use_failed` recovery** — Groq aborts a 200 stream with this code
   when llama emits malformed tool-call JSON. The step is retried up to 2×,
   and as a last resort the call is **salvaged from Groq's
   `failed_generation` payload** and executed anyway.
4. **Leaked-tool-syntax salvage** — llama sometimes prints its native tool
   markup as *text*: `<function.name>{…}</function>`, `<function=name>{…}`,
   `<function(name){…}`. The client holds back text that starts like this
   and parses it into real `ToolCallRequest`s (`parse_leaked_tool_calls`,
   brace-matched JSON so nested arguments work); if it turns out to be prose,
   it's flushed as text. The chat never sees raw markup.

If all of that fails, the WS layer maps the exception to a clear error frame
(`_describe_llm_error` in [ws.py](../../src/assistant/api/ws.py)) by walking the
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

## Tokens: real vs estimated

Every turn's stats line shows `prompt→completion tok`. The source:

- **Real** — the provider reported usage (`UsageEvent`); shown without a
  suffix. Groq reports usage on streams.
- **Estimated** — `(est)` suffix: `chars // 4` fallback, used for FakeLLM,
  providers that don't report, and the pydantic-ai backend (it runs its own
  model layer, so the wrapper can't see its usage — turn totals fall back to
  structural estimates).

`InstrumentedLLM` ([telemetry.py](../../src/assistant/telemetry.py)) accumulates
per-turn totals in a `ContextVar` (`TurnStats`): steps, LLM ms, tokens, and
whether anything was estimated. Those feed the `turn` WS frame, the
`turn.summary` log line, Prometheus (`assistant_tokens_total{direction}`),
and the audit record.

## Cost accounting

`estimate_cost_usd(model, prompt_tokens, completion_tokens)` prices tokens
against a small table (per 1M tokens, listed pay-as-you-go prices):

| Model | prompt $ | completion $ |
|---|---:|---:|
| llama-3.3-70b-versatile | 0.59 | 0.79 |
| llama-3.1-8b-instant | 0.05 | 0.08 |
| gpt-4o-mini | 0.15 | 0.60 |
| gpt-4o | 2.50 | 10.00 |
| gpt-4.1-mini | 0.40 | 1.60 |
| *(fake / unknown)* | 0 | 0 |

The number is **indicative**: Groq's free tier actually bills $0 — it shows
what the traffic *would* cost at list prices. Surfaces: `~$0.0026` in the
stats line (hidden when 0), `cost_usd` in the turn frame + audit record +
`turn.summary` log, and the `assistant_cost_usd_total{model}` counter
(per-day spend = `increase(assistant_cost_usd_total[1d])` in Prometheus).

## Groq free-tier limits (measured, not theoretical)

Two kinds of limits, and they produce identical 429s with different advice:

- **Per-minute** (requests + tokens): brief bursts hit it; the client's
  backoff usually rides it out invisibly.
- **Per-day tokens (TPD)**: `llama-3.3-70b-versatile` has **100 000
  tokens/day** — one long testing session can genuinely exhaust it (we did:
  `Used 99470`). No amount of seconds-waiting helps; the chat error passes
  through Groq's own message, which names the limit and the wait.

Practical playbook:
- Budget gone → `ASSISTANT_LLM_MODEL=llama-3.1-8b-instant` (separate, larger
  daily budget; noticeably weaker answers — the salvage layers matter more).
- Watch spend live: `assistant_tokens_total` rate in Grafana, or the stats
  line per turn. A tool-heavy turn can cost 4–15k prompt tokens because each
  LLM step re-sends the conversation plus tool results.
- The **duplicate-call guard** (chapter 06) exists exactly because repeated
  identical tool calls were the fastest way to burn the daily budget.

## Switching models — checklist

1. Edit `.env`: `ASSISTANT_LLM_MODEL=...` (and provider/key if changing
   provider). 2. Restart the server. 3. Send one message; check the stats
line: real tokens (no "(est)") confirm usage reporting works; the model name
shows in `/api/health` under `llm`. 4. If the model was a typo you'll get the
`model_unavailable` chat error naming it.
