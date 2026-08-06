"""ReAct-loop unit tests with a scripted LLM — every branch is deterministic."""

import json

from assistant.agent.backends.custom import CustomAgent
from assistant.agent.base import ChatMessage, FinalEvent, ToolCallEvent, ToolResultEvent
from assistant.agent.tools import Tool, ToolRegistry
from assistant.llm.client import LLMEvent, TextDelta, ToolCallRequest, ToolSpec


class ScriptedLLM:
    """Plays a fixed sequence of steps; the last step repeats if the loop continues."""

    def __init__(self, steps: list[list[LLMEvent]]) -> None:
        self._steps = steps
        self._cursor = 0

    async def stream_step(self, messages: list[ChatMessage], tools: list[ToolSpec] | None = None):
        step = self._steps[min(self._cursor, len(self._steps) - 1)]
        self._cursor += 1
        for event in step:
            yield event


def make_registry(record: list[dict[str, object]]) -> ToolRegistry:
    async def handler(arguments: dict[str, object]) -> str:
        record.append(arguments)
        return "tool says: 42"

    return ToolRegistry(
        [Tool(name="search_docs", description="d", parameters={"type": "object"}, handler=handler)]
    )


async def test_loop_executes_tool_then_finalizes():
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
    agent = CustomAgent(llm=llm, system_prompt="s", tools=make_registry(calls))
    events = [event async for event in agent.run(history=[], user_message="how do we deploy?")]

    assert isinstance(events[0], ToolCallEvent)
    assert isinstance(events[1], ToolResultEvent)
    assert events[1].result == "tool says: 42"
    assert isinstance(events[-1], FinalEvent)
    assert events[-1].content == "The answer is 42."
    assert calls == [{"query": "deploy"}]


async def test_loop_stops_at_max_iterations():
    # The single scripted step always requests a tool -> the loop can never finish naturally.
    llm = ScriptedLLM([[ToolCallRequest(id="c", name="search_docs", arguments="{}")]])
    agent = CustomAgent(llm=llm, system_prompt="s", tools=make_registry([]), max_iterations=3)
    events = [event async for event in agent.run(history=[], user_message="loop forever")]

    tool_calls = [event for event in events if isinstance(event, ToolCallEvent)]
    assert len(tool_calls) == 3
    assert isinstance(events[-1], FinalEvent)
    assert "limit" in events[-1].content.lower()


async def test_unknown_tool_reports_error_and_recovers():
    llm = ScriptedLLM(
        [
            [ToolCallRequest(id="c1", name="restart_prod", arguments="{}")],
            [TextDelta("I could not do that.")],
        ]
    )
    agent = CustomAgent(llm=llm, system_prompt="s", tools=make_registry([]))
    events = [event async for event in agent.run(history=[], user_message="x")]

    result = next(event for event in events if isinstance(event, ToolResultEvent))
    assert "unknown tool" in result.result
    assert isinstance(events[-1], FinalEvent)


async def test_malformed_json_arguments_degrade_to_empty_dict():
    calls: list[dict[str, object]] = []
    llm = ScriptedLLM(
        [
            [ToolCallRequest(id="c1", name="search_docs", arguments="{not valid json")],
            [TextDelta("done")],
        ]
    )
    agent = CustomAgent(llm=llm, system_prompt="s", tools=make_registry(calls))
    events = [event async for event in agent.run(history=[], user_message="x")]

    assert calls == [{}]
    assert isinstance(events[-1], FinalEvent)


async def test_crashing_tool_becomes_error_result_not_exception():
    async def exploding(arguments: dict[str, object]) -> str:
        raise RuntimeError("boom")

    registry = ToolRegistry(
        [
            Tool(
                name="search_docs",
                description="d",
                parameters={"type": "object"},
                handler=exploding,
            )
        ]
    )
    llm = ScriptedLLM(
        [
            [ToolCallRequest(id="c1", name="search_docs", arguments="{}")],
            [TextDelta("recovered")],
        ]
    )
    agent = CustomAgent(llm=llm, system_prompt="s", tools=registry)
    events = [event async for event in agent.run(history=[], user_message="x")]

    result = next(event for event in events if isinstance(event, ToolResultEvent))
    assert "failed" in result.result
    assert isinstance(events[-1], FinalEvent)
    assert events[-1].content == "recovered"
