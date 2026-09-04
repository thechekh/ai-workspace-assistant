"""Telemetry: Prometheus metrics, manual tracing spans, per-turn stats.

- Metrics are ALWAYS on (prometheus-client counters/histograms — negligible
  overhead), scraped from GET /metrics.
- Spans go through the global OpenTelemetry tracer. Until
  `observability.configure_observability` installs a provider (OTLP->Jaeger,
  Logfire, or Langfuse), the tracer is a no-op — instrumented code needs no
  guards and costs ~nothing.
- `InstrumentedLLM` wraps any LLMClient: a span + metrics per LLM step,
  token-usage capture (real via UsageEvent, estimated otherwise), and the
  optional full prompt/completion debug log.
"""

import time
from contextvars import ContextVar
from dataclasses import dataclass, field

import structlog
from opentelemetry import trace
from prometheus_client import Counter, Histogram

from assistant.agent.base import ChatMessage
from assistant.llm.client import LLMClient, TextDelta, ToolCallRequest, ToolSpec, UsageEvent

logger = structlog.get_logger("assistant.telemetry")
tracer = trace.get_tracer("assistant")

TURNS_TOTAL = Counter("assistant_turns_total", "Chat turns processed", ["backend"])
TURN_SECONDS = Histogram("assistant_turn_seconds", "End-to-end turn duration", ["backend"])
LLM_STEP_SECONDS = Histogram("assistant_llm_step_seconds", "One LLM step duration", ["provider"])
TOOL_SECONDS = Histogram("assistant_tool_seconds", "Tool execution duration", ["tool"])
TOOL_CALLS_TOTAL = Counter("assistant_tool_calls_total", "Tool calls", ["tool", "status"])
RETRIEVAL_SECONDS = Histogram("assistant_retrieval_seconds", "RAG retrieval duration", ["mode"])
TOKENS_TOTAL = Counter("assistant_tokens_total", "LLM tokens (real or estimated)", ["direction"])
ERRORS_TOTAL = Counter("assistant_errors_total", "Errors surfaced to clients", ["kind"])
COST_USD_TOTAL = Counter("assistant_cost_usd_total", "Indicative LLM spend in USD", ["model"])
CANCELLED_TOTAL = Counter(
    "assistant_cancelled_turns_total", "Turns stopped by the user", ["backend"]
)
RATE_LIMITED_TOTAL = Counter(
    "assistant_rate_limited_total", "Requests refused by the rate limiter", ["bucket"]
)

# Indicative $ per 1M tokens (prompt, completion) at the providers' listed
# pay-as-you-go prices. Free tiers actually bill $0 — the number shows what
# the traffic *would* cost. Unknown models (and the fake provider) price at 0.
MODEL_PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "gpt-4.1-nano": (0.10, 0.40),  # cheapest that still calls tools — the default
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
}


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    prompt_price, completion_price = MODEL_PRICES_PER_MTOK.get(model, (0.0, 0.0))
    return (prompt_tokens * prompt_price + completion_tokens * completion_price) / 1_000_000


@dataclass
class TurnStats:
    """Accumulated by InstrumentedLLM during one turn; read by the WS layer.

    `seen_tool_calls` backs Tool.run's duplicate-call guard: models sometimes
    repeat the exact same tool call within a turn,
    burning latency and rate limits for zero new information.
    """

    llm_steps: int = 0
    llm_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usage_estimated: bool = False
    seen_tool_calls: set[tuple[str, str]] = field(default_factory=set)


current_turn_stats: ContextVar[TurnStats | None] = ContextVar("current_turn_stats", default=None)


def record_external_usage(
    *, prompt_tokens: int, completion_tokens: int, steps: int, llm_ms: float, estimated: bool
) -> None:
    """Fold usage reported outside `InstrumentedLLM` into the same stats and counters.

    The Pydantic AI backend drives the provider through its own model layer,
    so its tokens never pass through `stream_step`; it reports them here once
    per run so the stats line, the cost and the token counters agree across
    all three backends. The counters stay in this module on purpose: LLM
    telemetry has one home, whichever code path produced the numbers.
    """
    TOKENS_TOTAL.labels(direction="prompt").inc(prompt_tokens)
    TOKENS_TOTAL.labels(direction="completion").inc(completion_tokens)
    stats = current_turn_stats.get()
    if stats is None:
        return
    stats.llm_steps += steps
    stats.llm_ms += llm_ms
    stats.prompt_tokens += prompt_tokens
    stats.completion_tokens += completion_tokens
    stats.usage_estimated = stats.usage_estimated or estimated


class InstrumentedLLM:
    """Telemetry decorator for any LLMClient (fake, OpenAI, Ollama, ...)."""

    def __init__(
        self, inner: LLMClient, *, provider: str, model: str, log_prompts: bool = False
    ) -> None:
        self._inner = inner
        self._provider = provider
        self._model = model
        self._log_prompts = log_prompts

    async def stream_step(self, messages: list[ChatMessage], tools: list[ToolSpec] | None = None):
        prompt_chars = sum(len(message.content) for message in messages)
        if self._log_prompts:
            logger.info(
                "llm.prompt",
                messages=[{"role": m.role, "content": m.content} for m in messages],
                tools=[tool.name for tool in tools or []],
            )

        start = time.perf_counter()
        text_chars = 0
        tool_calls = 0
        usage: UsageEvent | None = None
        completion_text: list[str] = []
        # Bound up front so the outer `finally` can never raise NameError and
        # mask the original exception if the span fails to open.
        duration = 0.0
        prompt_tokens = 0
        completion_tokens = 0

        # `finally` so an abandoned turn (client disconnects mid-stream ->
        # GeneratorExit/CancelledError at the `yield`) still records its cost
        # and latency instead of vanishing from metrics.
        try:
            with tracer.start_as_current_span("llm.step") as span:
                span.set_attribute("llm.provider", self._provider)
                span.set_attribute("llm.model", self._model)
                span.set_attribute("llm.prompt_messages", len(messages))
                try:
                    async for event in self._inner.stream_step(messages, tools):
                        if isinstance(event, UsageEvent):
                            usage = event  # consumed here; never forwarded to the agent loop
                            continue
                        if isinstance(event, TextDelta):
                            text_chars += len(event.text)
                            if self._log_prompts:
                                completion_text.append(event.text)
                        elif isinstance(event, ToolCallRequest):
                            tool_calls += 1
                        yield event
                finally:
                    duration = time.perf_counter() - start
                    prompt_tokens = usage.prompt_tokens if usage else prompt_chars // 4
                    completion_tokens = usage.completion_tokens if usage else text_chars // 4
                    span.set_attribute("llm.duration_ms", round(duration * 1000))
                    span.set_attribute("llm.tool_calls", tool_calls)
                    span.set_attribute("llm.completion_chars", text_chars)
                    span.set_attribute("llm.prompt_tokens", prompt_tokens)
                    span.set_attribute("llm.completion_tokens", completion_tokens)
                    span.set_attribute("llm.usage_estimated", usage is None)
        finally:
            LLM_STEP_SECONDS.labels(provider=self._provider).observe(duration)
            TOKENS_TOTAL.labels(direction="prompt").inc(prompt_tokens)
            TOKENS_TOTAL.labels(direction="completion").inc(completion_tokens)

            stats = current_turn_stats.get()
            if stats is not None:
                stats.llm_steps += 1
                stats.llm_ms += duration * 1000
                stats.prompt_tokens += prompt_tokens
                stats.completion_tokens += completion_tokens
                stats.usage_estimated = stats.usage_estimated or usage is None

            if self._log_prompts:
                logger.info(
                    "llm.completion",
                    text="".join(completion_text),
                    tool_calls=tool_calls,
                    duration_ms=round(duration * 1000),
                )
