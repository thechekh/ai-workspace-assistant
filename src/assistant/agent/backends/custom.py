"""Backend A — the hand-written ReAct loop.

One user turn: stream an LLM step; if the model requested tools, execute
them, append the results, and run another step — bounded by max_iterations.
A step with no tool calls is the final answer. No framework, no magic:
this is the mechanism the Pydantic AI and LangGraph backends wrap.
"""

import json
from collections.abc import AsyncGenerator

from assistant.agent.base import (
    ITERATION_LIMIT_MESSAGE,
    MAX_ITERATIONS,
    AgentEvent,
    ChatMessage,
    FinalEvent,
    TokenEvent,
    ToolCall,
    ToolCallEvent,
    ToolResultEvent,
    truncate_for_event,
)
from assistant.agent.tools import ToolRegistry
from assistant.llm.client import LLMClient, TextDelta, ToolCallRequest, aclose_iterator

# Tool output shown in the UI event is trimmed; the LLM always gets the full text.


def _parse_arguments(raw: str) -> dict[str, object]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class CustomAgent:
    def __init__(
        self,
        llm: LLMClient,
        system_prompt: str,
        tools: ToolRegistry | None = None,
        max_iterations: int = MAX_ITERATIONS,
    ) -> None:
        self._llm = llm
        self._system_prompt = system_prompt
        # Never None: an empty registry behaves identically and removes a
        # dead branch from the hot loop.
        self._tools = tools if tools is not None else ToolRegistry()
        self._max_iterations = max_iterations

    async def run(
        self, history: list[ChatMessage], user_message: str
    ) -> AsyncGenerator[AgentEvent, None]:
        messages = [
            ChatMessage(role="system", content=self._system_prompt),
            *history,
            ChatMessage(role="user", content=user_message),
        ]
        specs = self._tools.specs if len(self._tools) else None

        for _ in range(self._max_iterations):
            parts: list[str] = []
            requests: list[ToolCallRequest] = []
            step = self._llm.stream_step(messages, tools=specs)
            try:
                async for event in step:
                    if isinstance(event, TextDelta):
                        parts.append(event.text)
                        yield TokenEvent(content=event.text)
                    elif isinstance(event, ToolCallRequest):
                        requests.append(event)
                    # other event kinds (e.g. usage) are telemetry-only — ignore
            finally:
                # A stopped turn closes this generator mid-step; close the
                # provider stream here, in the same task, not at GC time.
                await aclose_iterator(step)
            text = "".join(parts)

            if not requests:
                yield FinalEvent(content=text)
                return

            messages.append(
                ChatMessage(
                    role="assistant",
                    content=text,
                    tool_calls=[
                        ToolCall(id=r.id, name=r.name, arguments=r.arguments) for r in requests
                    ],
                )
            )
            for request in requests:
                arguments = _parse_arguments(request.arguments)
                yield ToolCallEvent(tool=request.name, arguments=arguments)
                result = await self._tools.execute(request.name, arguments)
                yield ToolResultEvent(tool=request.name, result=truncate_for_event(result))
                messages.append(ChatMessage(role="tool", content=result, tool_call_id=request.id))

        yield FinalEvent(content=ITERATION_LIMIT_MESSAGE)
