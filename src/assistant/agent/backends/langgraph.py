"""Backend C — LangGraph.

Implements the same AgentBackend contract as the other two, as an explicit
two-node state graph:

    START -> agent -> (tool_calls? tools -> agent : END)

Tools come from the shared ToolRegistry (the `tools` node executes through
it directly), and the model side is a thin `BaseChatModel` adapter around our
provider-agnostic `LLMClient` protocol — so the offline FakeLLM, Groq, and
every other provider work here unchanged, and the scripted test LLMs run on
this backend too.

The graph is compiled with an InMemorySaver checkpointer and a fresh
thread_id per turn: cross-turn memory stays in Redis (shared by all
backends), while the checkpointer records per-turn graph state — LangGraph's
native persistence, demonstrated without diverging from the other runtimes.
"""

import json
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, MessagesState, StateGraph
from pydantic import PrivateAttr

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
from assistant.llm.client import LLMClient, TextDelta, ToolSpec

_EVENT_RESULT_LIMIT = 1500


def _lc_to_ours(messages: Sequence[BaseMessage]) -> list[ChatMessage]:
    """LangChain messages -> our ChatMessage shape (what LLMClient speaks)."""
    converted: list[ChatMessage] = []
    for message in messages:
        if isinstance(message, SystemMessage):
            converted.append(ChatMessage(role="system", content=str(message.content)))
        elif isinstance(message, HumanMessage):
            converted.append(ChatMessage(role="user", content=str(message.content)))
        elif isinstance(message, ToolMessage):
            converted.append(
                ChatMessage(
                    role="tool",
                    content=str(message.content),
                    tool_call_id=message.tool_call_id,
                )
            )
        elif isinstance(message, AIMessage):
            tool_calls = [
                ToolCall(
                    id=call["id"] or "",
                    name=call["name"],
                    arguments=json.dumps(call["args"]),
                )
                for call in message.tool_calls
            ]
            converted.append(
                ChatMessage(
                    role="assistant",
                    content=str(message.content),
                    tool_calls=tool_calls or None,
                )
            )
    return converted


class LLMClientChatModel(BaseChatModel):
    """LangChain chat-model adapter over our LLMClient protocol.

    One adapter covers every provider (fake/groq/ollama/gemini/openai) —
    LangGraph never talks to a vendor SDK directly.
    """

    _client: LLMClient = PrivateAttr()

    def __init__(self, client: LLMClient, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client = client

    @property
    def _llm_type(self) -> str:
        return "assistant-llm-client"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError("async-only model — use astream/ainvoke")

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        tool_dicts = kwargs.get("tools") or []
        specs = [
            ToolSpec(
                name=tool["function"]["name"],
                description=tool["function"].get("description", ""),
                parameters=dict(tool["function"].get("parameters", {})),
            )
            for tool in tool_dicts
        ]
        call_index = 0
        async for event in self._client.stream_step(_lc_to_ours(messages), tools=specs or None):
            if isinstance(event, TextDelta):
                yield ChatGenerationChunk(message=AIMessageChunk(content=event.text))
            else:
                chunk = AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": event.name,
                            "args": event.arguments,
                            "id": event.id or f"call_{call_index}",
                            "index": call_index,
                            "type": "tool_call_chunk",
                        }
                    ],
                )
                call_index += 1
                yield ChatGenerationChunk(message=chunk)


class LangGraphAgent:
    def __init__(
        self,
        llm: LLMClient,
        system_prompt: str,
        tools: ToolRegistry | None = None,
        max_iterations: int = 6,
    ) -> None:
        self._system_prompt = system_prompt
        self._tools = tools
        self._max_iterations = max_iterations

        model = LLMClientChatModel(llm)
        if tools and len(tools):
            openai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.parameters,
                    },
                }
                for spec in tools.specs
            ]
            self._model = model.bind(tools=openai_tools)
        else:
            self._model = model

        self._graph = self._build_graph()

    def _build_graph(self):
        async def agent_node(state: MessagesState) -> dict[str, list[BaseMessage]]:
            # Public astream fires token callbacks -> graph stream_mode="messages"
            response: AIMessageChunk | None = None
            async for chunk in self._model.astream(state["messages"]):
                assert isinstance(chunk, AIMessageChunk)
                response = chunk if response is None else response + chunk
            return {"messages": [response or AIMessageChunk(content="")]}

        async def tools_node(state: MessagesState) -> dict[str, list[BaseMessage]]:
            last = state["messages"][-1]
            assert isinstance(last, AIMessage)
            results: list[BaseMessage] = []
            for call in last.tool_calls:
                if self._tools is None:
                    result = f"error: unknown tool {call['name']!r}"
                else:
                    result = await self._tools.execute(call["name"], dict(call["args"]))
                results.append(
                    ToolMessage(content=result, tool_call_id=call["id"] or "", name=call["name"])
                )
            return {"messages": results}

        def route_after_agent(state: MessagesState) -> str:
            last = state["messages"][-1]
            has_calls = isinstance(last, AIMessage) and bool(last.tool_calls)
            return "tools" if has_calls else END

        builder = StateGraph(MessagesState)
        builder.add_node("agent", agent_node)
        builder.add_node("tools", tools_node)
        builder.add_edge(START, "agent")
        builder.add_conditional_edges("agent", route_after_agent, {"tools": "tools", END: END})
        builder.add_edge("tools", "agent")
        # Per-turn checkpointing: native LangGraph persistence for state
        # inspection, while cross-turn memory stays in shared Redis.
        return builder.compile(checkpointer=InMemorySaver())

    async def run(self, history: list[ChatMessage], user_message: str) -> AsyncIterator[AgentEvent]:
        lc_history: list[BaseMessage] = [SystemMessage(content=self._system_prompt)]
        for message in history:
            if message.role == "user":
                lc_history.append(HumanMessage(content=message.content))
            elif message.role == "assistant":
                lc_history.append(AIMessage(content=message.content))
            elif message.role == "system":
                # e.g. the rolling conversation summary injected by ConversationMemory
                lc_history.append(SystemMessage(content=message.content))
        lc_history.append(HumanMessage(content=user_message))

        config: Any = {
            "configurable": {"thread_id": uuid.uuid4().hex},
            # agent + tools alternate: 2 super-steps per LLM iteration
            "recursion_limit": 2 * self._max_iterations,
        }

        final_text = ""
        try:
            async for mode, payload in self._graph.astream(
                cast("MessagesState", {"messages": lc_history}),
                config,
                stream_mode=["messages", "updates"],
            ):
                if mode == "messages":
                    chunk, _metadata = payload
                    if (
                        isinstance(chunk, AIMessageChunk)
                        and isinstance(chunk.content, str)
                        and chunk.content
                    ):
                        yield TokenEvent(content=chunk.content)
                    continue
                if not isinstance(payload, dict):
                    continue
                for _node_name, delta in payload.items():
                    for message in delta.get("messages") or []:
                        if isinstance(message, AIMessage):
                            if message.tool_calls:
                                for call in message.tool_calls:
                                    yield ToolCallEvent(
                                        tool=call["name"], arguments=dict(call["args"])
                                    )
                            else:
                                final_text = str(message.content)
                        elif isinstance(message, ToolMessage):
                            text = str(message.content)
                            if len(text) > _EVENT_RESULT_LIMIT:
                                text = text[:_EVENT_RESULT_LIMIT] + "…"
                            yield ToolResultEvent(tool=message.name or "unknown", result=text)
        except GraphRecursionError:
            yield FinalEvent(
                content=(
                    "I hit the tool-call limit for one turn without reaching a final "
                    "answer. Please rephrase or narrow the question."
                )
            )
            return
        yield FinalEvent(content=final_text)
