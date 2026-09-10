"""Direct tests for the Pydantic AI backend (offline FunctionModel fake)."""

from collections.abc import AsyncIterator

import httpx2
import pytest
from openai import RateLimitError
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from assistant.agent.backends.pydantic_ai import PydanticAIAgent, build_pydantic_model
from assistant.agent.base import (
    AgentEvent,
    ChatMessage,
    FinalEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from assistant.agent.tools import ToolRegistry, make_search_docs
from tests.conftest import HermeticSettings, build_seeded_retriever_async, make_registry


def make_agent(tools: ToolRegistry | None = None) -> PydanticAIAgent:
    settings = HermeticSettings(llm_provider="fake")
    return PydanticAIAgent(
        model=build_pydantic_model(settings), system_prompt="system prompt", tools=tools
    )


async def test_plain_chat_streams_and_finalizes():
    agent = make_agent()
    events = [event async for event in agent.run(history=[], user_message="hello")]
    tokens = [event.content for event in events if isinstance(event, TokenEvent)]
    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert "".join(tokens) == final.content
    assert "You said: hello" in final.content


async def test_history_reaches_the_model():
    agent = make_agent()
    history = [
        ChatMessage(role="user", content="earlier question"),
        ChatMessage(role="assistant", content="earlier answer"),
    ]
    events = [event async for event in agent.run(history=history, user_message="follow-up")]
    final = events[-1]
    assert isinstance(final, FinalEvent)
    # system + user | assistant | user -> 4 parts, matching the custom backend
    assert "(4 messages in context)" in final.content


async def test_tool_loop_through_shared_registry():
    registry = ToolRegistry([make_search_docs(await build_seeded_retriever_async())])
    agent = make_agent(tools=registry)
    events = [
        event
        async for event in agent.run(
            history=[], user_message="Which service generates PDF invoices?"
        )
    ]

    tool_call = next(event for event in events if isinstance(event, ToolCallEvent))
    assert tool_call.tool == "search_docs"
    tool_result = next(event for event in events if isinstance(event, ToolResultEvent))
    assert "billing-service" in tool_result.result
    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert "Based on the tool results" in final.content


async def test_run_reports_usage_into_the_turn_stats():
    """This backend bypasses InstrumentedLLM; before it reported its own
    usage, the stats line, the cost and the token counters read near zero for
    one backend out of three. Langfuse showed the truth: ~5,000 prompt tokens
    per call that the UI called 0."""
    from assistant.telemetry import TurnStats, current_turn_stats

    stats = TurnStats()
    token = current_turn_stats.set(stats)
    try:
        agent = make_agent()
        events = [event async for event in agent.run(history=[], user_message="hello")]
    finally:
        current_turn_stats.reset(token)

    assert isinstance(events[-1], FinalEvent)
    assert stats.llm_steps == 1
    assert stats.prompt_tokens > 0
    assert stats.completion_tokens > 0
    assert stats.llm_ms >= 0
    # The FunctionModel fake estimates its counts; a real provider reports them.
    assert stats.usage_estimated is True


def _looping_model():
    """A model that requests the same tool on every step, forever."""
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    async def always_call(messages, info):
        yield {0: DeltaToolCall(name="search_docs", json_args='{"query": "again"}')}

    return FunctionModel(stream_function=always_call, model_name="looper")


async def test_a_looping_model_hits_the_shared_iteration_limit():
    """Parity: the custom loop and LangGraph stop after MAX_ITERATIONS steps
    with ITERATION_LIMIT_MESSAGE. Without a request limit, pydantic-ai's own
    default (50 requests) applied here, and the UsageLimitExceeded it raised
    reached the user as a generic server error."""
    from assistant.agent.base import ITERATION_LIMIT_MESSAGE, MAX_ITERATIONS
    from assistant.telemetry import TurnStats, current_turn_stats

    calls: list[dict[str, object]] = []
    agent = PydanticAIAgent(model=_looping_model(), system_prompt="s", tools=make_registry(calls))
    stats = TurnStats()
    token = current_turn_stats.set(stats)
    try:
        events = [event async for event in agent.run(history=[], user_message="loop")]
    finally:
        current_turn_stats.reset(token)

    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert final.content == ITERATION_LIMIT_MESSAGE
    # The duplicate-call guard answers repeats without re-running the tool,
    # but every step still counts against the limit.
    assert len([e for e in events if isinstance(e, ToolCallEvent)]) == MAX_ITERATIONS
    # And the tokens spent getting there are reported, not lost.
    assert stats.llm_steps == MAX_ITERATIONS
    assert stats.prompt_tokens > 0


# --- provider flakes: the retries this backend re-implements ------------------
# It drives the provider through pydantic-ai's own model layer, so it inherits
# none of `llm/client.py`'s hardening and repeats it. Everything else in the
# suite tests that hardening on the *client*; these test the copy, because a
# copy that quietly stopped working is exactly the parity gap it exists to
# close — observed live as a 429 and a `tool_use_failed` turning a good
# question into an error here while the other two backends recovered.


def _rate_limited(retry_after: str = "0") -> RateLimitError:
    request = httpx2.Request("POST", "https://api.test/v1/chat/completions")
    response = httpx2.Response(429, request=request, headers={"retry-after": retry_after})
    return RateLimitError("slow down", response=response, body=None)


def _tool_use_failed() -> Exception:
    """What a provider raises when the model emits unusable tool-call JSON."""
    return RuntimeError("Failed to call a function. Please adjust your prompt.")


def _flaky_model(failures: list[BaseException], *, answer: str = "recovered") -> FunctionModel:
    """Raises each queued failure on successive attempts, then answers."""
    queue = list(failures)

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        if queue:
            raise queue.pop(0)
        yield answer

    return FunctionModel(stream_function=stream, model_name="flaky")


@pytest.mark.parametrize(
    ("failure", "label"),
    [(_rate_limited(), "429"), (_tool_use_failed(), "tool_use_failed")],
)
async def test_a_provider_flake_before_the_first_token_is_retried(
    failure: BaseException, label: str
):
    agent = PydanticAIAgent(model=_flaky_model([failure]), system_prompt="s")
    events = [event async for event in agent.run(history=[], user_message="hi")]
    final = events[-1]
    assert isinstance(final, FinalEvent), f"{label} should have been retried, not raised"
    assert final.content == "recovered"


async def test_the_retry_budget_is_finite():
    """Three 429s in a row exhaust the two retries and surface the error, so a
    dead provider fails the turn instead of looping."""
    agent = PydanticAIAgent(
        model=_flaky_model([_rate_limited(), _rate_limited(), _rate_limited()]), system_prompt="s"
    )
    with pytest.raises(RateLimitError):
        _ = [event async for event in agent.run(history=[], user_message="hi")]


async def test_a_flake_after_the_first_token_is_not_retried():
    """Retrying once output is on screen would duplicate it — the same
    `emitted` guard `OpenAICompatibleLLM.stream_step` uses."""

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        yield "half an answer"
        raise _rate_limited()

    agent = PydanticAIAgent(
        model=FunctionModel(stream_function=stream, model_name="dies-midstream"), system_prompt="s"
    )
    seen: list[AgentEvent] = []

    async def drain() -> None:
        async for event in agent.run(history=[], user_message="hi"):
            seen.append(event)

    with pytest.raises(RateLimitError):
        await drain()
    assert any(isinstance(event, TokenEvent) for event in seen), "the partial answer was streamed"


async def test_an_unrecognised_provider_error_is_not_retried():
    """Only the two known flakes are retried; anything else fails fast."""
    agent = PydanticAIAgent(model=_flaky_model([ValueError("bad request")]), system_prompt="s")
    with pytest.raises(ValueError, match="bad request"):
        _ = [event async for event in agent.run(history=[], user_message="hi")]


async def test_usage_is_recorded_when_the_turn_is_abandoned_early():
    """Stopping mid-turn closes the generator; the spend must still land in
    the turn stats, like InstrumentedLLM records in a `finally`."""
    from assistant.telemetry import TurnStats, current_turn_stats

    agent = PydanticAIAgent(model=_looping_model(), system_prompt="s", tools=make_registry([]))
    stats = TurnStats()
    token = current_turn_stats.set(stats)
    try:
        run = agent.run(history=[], user_message="loop")
        first = await anext(run)
        assert isinstance(first, ToolCallEvent)
        await run.aclose()  # what a cancelled WebSocket turn does
    finally:
        current_turn_stats.reset(token)

    assert stats.llm_steps >= 1
    assert stats.prompt_tokens > 0
