"""Direct tests for the LangGraph backend.

The BaseChatModel adapter speaks our LLMClient protocol, so the same
ScriptedLLM used against the custom loop drives this backend too.
"""

import json

from assistant.agent.backends.langgraph import LangGraphAgent
from assistant.agent.base import (
    ChatMessage,
    FinalEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from assistant.agent.tools import ToolRegistry, make_search_docs
from assistant.llm.client import FakeLLM, TextDelta, ToolCallRequest
from tests.conftest import (
    HermeticSettings,
    ScriptedLLM,
    build_seeded_retriever_async,
    make_registry,
)


async def test_plain_chat_streams_and_finalizes():
    agent = LangGraphAgent(llm=FakeLLM(), system_prompt="system prompt")
    events = [event async for event in agent.run(history=[], user_message="hello")]
    tokens = [event.content for event in events if isinstance(event, TokenEvent)]
    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert "".join(tokens) == final.content
    assert "You said: hello" in final.content


async def test_history_reaches_the_model():
    agent = LangGraphAgent(llm=FakeLLM(), system_prompt="system prompt")
    history = [
        ChatMessage(role="user", content="earlier question"),
        ChatMessage(role="assistant", content="earlier answer"),
    ]
    events = [event async for event in agent.run(history=history, user_message="follow-up")]
    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert "(4 messages in context)" in final.content


async def test_scripted_tool_loop_executes_through_registry():
    calls: list[dict[str, object]] = []
    llm = ScriptedLLM(
        [
            [
                ToolCallRequest(
                    id="c1", name="search_docs", arguments=json.dumps({"query": "deploy"})
                )
            ],
            [TextDelta("The answer "), TextDelta("is 42.")],
        ]
    )
    agent = LangGraphAgent(llm=llm, system_prompt="s", tools=make_registry(calls))
    events = [event async for event in agent.run(history=[], user_message="how do we deploy?")]

    tool_call = next(event for event in events if isinstance(event, ToolCallEvent))
    assert tool_call.tool == "search_docs"
    assert tool_call.arguments == {"query": "deploy"}
    tool_result = next(event for event in events if isinstance(event, ToolResultEvent))
    assert tool_result.result == "tool says: 42"
    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert final.content == "The answer is 42."
    assert calls == [{"query": "deploy"}]


async def test_recursion_limit_bounds_the_loop():
    llm = ScriptedLLM([[ToolCallRequest(id="c", name="search_docs", arguments="{}")]])
    agent = LangGraphAgent(llm=llm, system_prompt="s", tools=make_registry([]), max_iterations=3)
    events = [event async for event in agent.run(history=[], user_message="loop forever")]
    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert "limit" in final.content.lower()


async def test_rag_tool_loop_end_to_end():
    settings = HermeticSettings(llm_provider="fake")
    del settings  # backend takes the FakeLLM directly; settings kept for parity clarity
    registry = ToolRegistry([make_search_docs(await build_seeded_retriever_async())])
    agent = LangGraphAgent(llm=FakeLLM(), system_prompt="sys", tools=registry)
    events = [
        event
        async for event in agent.run(
            history=[], user_message="Which service generates PDF invoices?"
        )
    ]
    tool_result = next(event for event in events if isinstance(event, ToolResultEvent))
    assert "billing-service" in tool_result.result
    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert "Based on the tool results" in final.content
