"""Backend A — the hand-written ReAct loop.

One user turn: stream an LLM step; if the model requested tools, execute
them, append the results, and run another step — bounded by max_iterations.
A step with no tool calls is the final answer. No framework, no magic:
this is the mechanism Pydantic AI (Phase 5) and LangGraph (Phase 6) wrap.
"""

import json
from collections.abc import AsyncIterator

from assistant.agent.base import (
    AgentEvent,
    ChatMessage,
    FinalEvent,
    TokenEvent,
    ToolCall,
    ToolCallEvent,
    ToolResultEvent,
)
from assistant.agent.tools import ToolRegistry
from assistant.llm.client import LLMClient, TextDelta, ToolCallRequest

# Tool output shown in the UI event is trimmed; the LLM always gets the full text.
_EVENT_RESULT_LIMIT = 1500


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
        max_iterations: int = 6,
    ) -> None:
        self._llm = llm
        self._system_prompt = system_prompt
        self._tools = tools
        self._max_iterations = max_iterations

    async def run(self, history: list[ChatMessage], user_message: str) -> AsyncIterator[AgentEvent]:
        messages = [
            ChatMessage(role="system", content=self._system_prompt),
            *history,
            ChatMessage(role="user", content=user_message),
        ]
        specs = self._tools.specs if self._tools and len(self._tools) else None

        for _ in range(self._max_iterations):
            parts: list[str] = []
            requests: list[ToolCallRequest] = []
            async for event in self._llm.stream_step(messages, tools=specs):
                if isinstance(event, TextDelta):
                    parts.append(event.text)
                    yield TokenEvent(content=event.text)
                elif isinstance(event, ToolCallRequest):
                    requests.append(event)
                # other event kinds (e.g. usage) are telemetry-only — ignore
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
                if self._tools is None:
                    result = f"error: unknown tool {request.name!r}"
                else:
                    result = await self._tools.execute(request.name, arguments)
                shown = (
                    result
                    if len(result) <= _EVENT_RESULT_LIMIT
                    else result[:_EVENT_RESULT_LIMIT] + "…"
                )
                yield ToolResultEvent(tool=request.name, result=shown)
                messages.append(ChatMessage(role="tool", content=result, tool_call_id=request.id))

        yield FinalEvent(
            content=(
                "I hit the tool-call limit for one turn without reaching a final "
                "answer. Please rephrase or narrow the question."
            )
        )
