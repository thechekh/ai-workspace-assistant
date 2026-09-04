"""Direct tests for the Pydantic AI backend (offline FunctionModel fake)."""

from assistant.agent.backends.pydantic_ai import PydanticAIAgent, build_pydantic_model
from assistant.agent.base import ChatMessage, FinalEvent, TokenEvent, ToolCallEvent, ToolResultEvent
from assistant.agent.tools import ToolRegistry, make_search_docs
from tests.conftest import HermeticSettings, build_seeded_retriever_async


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
