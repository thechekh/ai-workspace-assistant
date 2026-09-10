"""Per-turn accounting, without a WebSocket.

`TurnRecorder` was split out of `_handle_turn` precisely so the accounting
could be checked on its own; until now it was only ever exercised through the
WS suite, which never drives an error to completion — so the branch that
counts failures and writes the `error` audit row had no test at all.
"""

from assistant.agent.base import (
    ErrorEvent,
    FinalEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from assistant.api.turn_recorder import TurnRecorder


def _recorder() -> TurnRecorder:
    return TurnRecorder(turn_id="t1", backend="custom", llm_model="gpt-4.1-nano")


def test_a_full_turn_becomes_a_timeline_and_a_summary():
    recorder = _recorder()
    recorder.observe(TokenEvent(content="Hello "))
    recorder.observe(TokenEvent(content="world"))
    recorder.observe(ToolCallEvent(tool="search_docs", arguments={"query": "invoices"}))
    recorder.observe(ToolResultEvent(tool="search_docs", result="four chunks"))
    recorder.observe(FinalEvent(content="Hello world"))

    assert recorder.answer_chars == len("Hello world")
    assert recorder.streamed_text == "Hello world"
    assert recorder.first_token_ms is not None  # set by the first token, not the last
    assert recorder.tool_calls == ["search_docs"]
    assert [event.type for event in recorder.events] == [
        "tool_call",
        "tool_result",
        "final",
    ]

    summary = recorder.summary()
    assert summary.turn_id == "t1"
    assert summary.tool_calls == ["search_docs"]
    # No InstrumentedLLM ran, so the counts are the structural estimates.
    assert summary.usage_estimated is True
    assert summary.llm_steps == 2  # one call per tool, plus the answering step
    assert recorder.record(summary).events == recorder.events


def test_an_error_is_counted_and_written_to_the_timeline():
    """The failure path the UI reads: error_count drives the metric, and the
    audit row is what `GET /api/sessions/{id}/turns` replays."""
    recorder = _recorder()
    recorder.observe(TokenEvent(content="partial"))
    recorder.observe(ErrorEvent(message="the provider rejected the request"))

    assert recorder.error_count == 1
    # The partial answer survives: a failed turn still shows what was streamed.
    assert recorder.streamed_text == "partial"
    error_event = recorder.events[-1]
    assert error_event.type == "error"
    assert error_event.message == "the provider rejected the request"
    assert recorder.summary(failed=True).failed is True


def test_a_long_error_message_is_truncated_in_the_audit_row():
    """Audit rows round-trip through Redis; an unbounded provider message
    would be stored verbatim, once per failed turn."""
    recorder = _recorder()
    recorder.observe(ErrorEvent(message="x" * 5000))

    stored = recorder.events[-1].message
    assert stored is not None
    assert len(stored) == 300
