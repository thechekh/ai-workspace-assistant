"""Accumulates everything observable about one agent turn.

`_handle_turn` used to interleave four jobs in a single event loop: forward
frames to the socket, build the audit timeline, count tokens/latency, and
assemble the summary. That made every new event kind or metric an edit to the
same 150-line function, and made the accounting untestable without a live
WebSocket.

`TurnRecorder` owns the accounting half. Feed it each agent event with
`observe()`; ask it for a `TurnSummary` (the WS frame) and a `TurnRecord`
(the persisted audit row) at the end. It never touches the socket or Redis.
"""

import time

from assistant.agent.base import (
    AgentEvent,
    ErrorEvent,
    FinalEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from assistant.api.schemas import TurnAuditEvent, TurnRecord, TurnSummary
from assistant.telemetry import TurnStats, estimate_cost_usd

_ARGUMENTS_PREVIEW_CHARS = 300


class TurnRecorder:
    def __init__(self, *, turn_id: str, backend: str, llm_model: str) -> None:
        self.turn_id = turn_id
        self.backend = backend
        self._llm_model = llm_model
        self._started = time.perf_counter()

        self.stats = TurnStats()  # filled in by InstrumentedLLM via the contextvar
        self.tool_calls: list[str] = []
        self.events: list[TurnAuditEvent] = []
        self.first_token_ms: int | None = None
        self.answer_chars = 0
        self.error_count = 0

    def elapsed_ms(self) -> int:
        return round((time.perf_counter() - self._started) * 1000)

    def observe(self, event: AgentEvent) -> None:
        """Record one agent event. Pure accounting — no I/O."""
        now = self.elapsed_ms()

        if isinstance(event, TokenEvent):
            if self.first_token_ms is None:
                self.first_token_ms = now
            self.answer_chars += len(event.content)

        elif isinstance(event, ToolCallEvent):
            self.tool_calls.append(event.tool)
            self.events.append(
                TurnAuditEvent(
                    ms=now,
                    type="tool_call",
                    tool=event.tool,
                    arguments=str(event.arguments)[:_ARGUMENTS_PREVIEW_CHARS],
                )
            )

        elif isinstance(event, ToolResultEvent):
            self.events.append(
                TurnAuditEvent(
                    ms=now,
                    type="tool_result",
                    tool=event.tool,
                    result_chars=len(event.result),
                )
            )

        elif isinstance(event, FinalEvent):
            self.events.append(TurnAuditEvent(ms=now, type="final", chars=len(event.content)))

        elif isinstance(event, ErrorEvent):
            self.error_count += 1
            self.events.append(
                TurnAuditEvent(
                    ms=now, type="error", message=event.message[:_ARGUMENTS_PREVIEW_CHARS]
                )
            )

    def summary(self) -> TurnSummary:
        """The `turn` frame the UI renders as a stats line."""
        # The pydantic-ai backend runs its own model layer (not InstrumentedLLM),
        # so fall back to structural estimates when the wrapper saw nothing.
        completion_tokens = self.stats.completion_tokens or self.answer_chars // 4
        return TurnSummary(
            turn_id=self.turn_id,
            backend=self.backend,
            duration_ms=self.elapsed_ms(),
            first_token_ms=self.first_token_ms,
            llm_steps=self.stats.llm_steps or len(self.tool_calls) + 1,
            tool_calls=self.tool_calls,
            prompt_tokens=self.stats.prompt_tokens,
            completion_tokens=completion_tokens,
            usage_estimated=self.stats.usage_estimated or self.stats.llm_steps == 0,
            cost_usd=round(
                estimate_cost_usd(self._llm_model, self.stats.prompt_tokens, completion_tokens), 6
            ),
        )

    def record(self, summary: TurnSummary) -> TurnRecord:
        """The persisted audit row: the summary plus the event timeline."""
        return TurnRecord(
            **summary.model_dump(exclude={"type"}),
            events=self.events,
        )
