"""Backend B — Pydantic AI.

Implements the same AgentBackend contract as the custom loop, driven by
pydantic-ai's graph iteration API (`agent.iter`). Tools come from the shared
ToolRegistry (including the MCP-adapted ones) via `Tool.from_schema`, so all
backends expose identical capabilities — deliberately, for an apples-to-apples
comparison. (pydantic-ai also has native MCP client support; we route through
the shared registry instead so the three backends stay interchangeable.)

The `fake` LLM provider maps to a pydantic-ai FunctionModel that mirrors
FakeLLM's demo heuristics — the whole backend runs offline at zero cost.
"""

import json
import re
from collections.abc import AsyncIterator

from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    SystemPromptPart,
    TextPart,
    TextPartDelta,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import Tool as PydanticTool

from assistant.agent.base import (
    AgentEvent,
    ChatMessage,
    FinalEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from assistant.agent.tools import Tool as RegistryTool
from assistant.agent.tools import ToolRegistry
from assistant.config import Settings
from assistant.llm.client import PROVIDER_BASE_URLS, _stream_words

_EVENT_RESULT_LIMIT = 1500


def _adapt_tool(tool: RegistryTool) -> PydanticTool:
    """Wrap a registry tool (JSON schema + async handler) as a pydantic-ai Tool."""

    async def call(**kwargs: object) -> str:
        return await tool.handler(dict(kwargs))

    return PydanticTool.from_schema(
        call,
        name=tool.name,
        description=tool.description,
        json_schema=dict(tool.parameters),
    )


def _to_model_messages(system_prompt: str, history: list[ChatMessage]) -> list[ModelMessage]:
    """Map our plain text history to pydantic-ai messages.

    pydantic-ai does not re-apply `system_prompt` when a message_history is
    given, so the system prompt is folded into the first request.
    """
    messages: list[ModelMessage] = []
    pending_system: SystemPromptPart | None = SystemPromptPart(content=system_prompt)
    for message in history:
        if message.role == "user":
            parts: list[SystemPromptPart | UserPromptPart] = [
                UserPromptPart(content=message.content)
            ]
            if pending_system is not None:
                parts.insert(0, pending_system)
                pending_system = None
            messages.append(ModelRequest(parts=parts))
        elif message.role == "assistant":
            messages.append(ModelResponse(parts=[TextPart(content=message.content)]))
        elif message.role == "system":
            # e.g. the rolling conversation summary injected by ConversationMemory
            messages.append(ModelRequest(parts=[SystemPromptPart(content=message.content)]))
    if pending_system is not None and messages:
        # History had no user turn to carry the system prompt — prepend it.
        messages.insert(0, ModelRequest(parts=[pending_system]))
    return messages


class PydanticAIAgent:
    def __init__(
        self, model: Model | str, system_prompt: str, tools: ToolRegistry | None = None
    ) -> None:
        self._system_prompt = system_prompt
        self._agent = PydanticAgent(
            model,
            system_prompt=system_prompt,
            tools=[_adapt_tool(tool) for tool in (tools.tools if tools else [])],
        )

    async def run(self, history: list[ChatMessage], user_message: str) -> AsyncIterator[AgentEvent]:
        message_history = _to_model_messages(self._system_prompt, history) if history else None
        async with self._agent.iter(user_message, message_history=message_history) as agent_run:
            async for node in agent_run:
                if PydanticAgent.is_model_request_node(node):
                    async with node.stream(agent_run.ctx) as request_stream:
                        async for event in request_stream:
                            if (
                                isinstance(event, PartStartEvent)
                                and isinstance(event.part, TextPart)
                                and event.part.content
                            ):
                                yield TokenEvent(content=event.part.content)
                            elif (
                                isinstance(event, PartDeltaEvent)
                                and isinstance(event.delta, TextPartDelta)
                                and event.delta.content_delta
                            ):
                                yield TokenEvent(content=event.delta.content_delta)
                elif PydanticAgent.is_call_tools_node(node):
                    async with node.stream(agent_run.ctx) as tools_stream:
                        async for event in tools_stream:
                            if isinstance(event, FunctionToolCallEvent):
                                yield ToolCallEvent(
                                    tool=event.part.tool_name,
                                    arguments=event.part.args_as_dict(),
                                )
                            elif isinstance(event, FunctionToolResultEvent):
                                part = event.result
                                text = (
                                    str(part.content)
                                    if isinstance(part, ToolReturnPart)
                                    else f"error: {part.model_response()}"
                                )
                                if len(text) > _EVENT_RESULT_LIMIT:
                                    text = text[:_EVENT_RESULT_LIMIT] + "…"
                                yield ToolResultEvent(tool=part.tool_name or "unknown", result=text)
        output = agent_run.result.output if agent_run.result is not None else ""
        yield FinalEvent(content=output if isinstance(output, str) else str(output))


async def _fake_stream(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    """FunctionModel twin of FakeLLM: same demo heuristics, zero cost, offline."""
    tool_names = {tool.name for tool in info.function_tools}
    last = messages[-1]
    last_part = last.parts[-1] if last.parts else None

    if isinstance(last_part, ToolReturnPart):
        snippet = " ".join(str(last_part.content).split())[:400]
        for piece in _stream_words(f"[fake-llm] Based on the tool results: {snippet}"):
            yield piece
        return

    user_text = ""
    for part in reversed(last.parts):
        if isinstance(part, UserPromptPart):
            user_text = str(part.content)
            break
    lowered = user_text.lower()

    if "github__list_pull_requests" in tool_names and (
        "pull request" in lowered or re.search(r"\bprs?\b", lowered)
    ):
        yield {0: DeltaToolCall(name="github__list_pull_requests", json_args="{}")}
        return

    if "code__search_code" in tool_names and "search code" in lowered:
        start = lowered.find("search code") + len("search code")
        pattern = user_text[start:].strip()
        if pattern.lower().startswith("for "):
            pattern = pattern[4:]
        pattern = pattern.strip(" ?.\"'") or "def "
        yield {
            0: DeltaToolCall(name="code__search_code", json_args=json.dumps({"pattern": pattern}))
        }
        return

    if "search_docs" in tool_names and user_text.rstrip().endswith("?"):
        yield {0: DeltaToolCall(name="search_docs", json_args=json.dumps({"query": user_text}))}
        return

    total_parts = sum(len(message.parts) for message in messages)
    reply = f"[fake-llm] ({total_parts} messages in context) You said: {user_text}"
    for piece in _stream_words(reply):
        yield piece


def build_pydantic_model(settings: Settings) -> Model:
    """Settings -> pydantic-ai model, mirroring llm.client.build_llm exactly."""
    provider = settings.llm_provider
    if provider == "fake":
        return FunctionModel(stream_function=_fake_stream, model_name="fake-llm")

    api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
    if provider == "ollama":
        api_key = api_key or "ollama"
    if api_key is None:
        raise ValueError(f"ASSISTANT_LLM_API_KEY is required for provider {provider!r}")

    base_url = settings.llm_base_url or PROVIDER_BASE_URLS[provider]
    openai_provider = (
        OpenAIProvider(base_url=base_url, api_key=api_key)
        if base_url is not None
        else OpenAIProvider(api_key=api_key)
    )
    return OpenAIChatModel(settings.llm_model, provider=openai_provider)
