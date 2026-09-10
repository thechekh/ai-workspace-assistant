"""Backend C — LangGraph.

Implements the same AgentBackend contract as the other two, as an explicit
two-node state graph:

    START -> agent -> (tool_calls? tools -> agent : END)

Tools come from the shared ToolRegistry (the `tools` node executes through
it directly), and the model side is a thin `BaseChatModel` adapter around our
provider-agnostic `LLMClient` protocol — so the offline FakeLLM, OpenAI, and
every other provider work here unchanged, and the scripted test LLMs run on
this backend too.

The graph is compiled with an InMemorySaver checkpointer and a fresh
thread_id per turn: cross-turn memory stays in Redis (shared by all
backends), while the checkpointer records per-turn graph state — LangGraph's
native persistence, demonstrated without diverging from the other runtimes.
The thread is deleted when the turn ends: an in-process saver that keeps
every turn's checkpoints (full history plus tool results) would grow until
restart.

One visible difference from the custom loop: graph "updates" arrive per node,
so a step that requests several tools emits all of its tool_call events
before any tool_result. The custom loop interleaves call and result per tool.
Consumers pair results to calls by name, first pending first.
"""

import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
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
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError

# `langgraph` is a PEP 420 namespace package split across several
# distributions, and the one holding `langgraph.graph` ships no `py.typed`
# marker — so pyright infers what it can from the source and leaves the
# rest Unknown. That is why the graph-building calls below carry ignores:
# they are the boundary this adapter exists to cross, not our own types.
from langgraph.graph import (  # pyright: ignore[reportMissingTypeStubs]
    END,
    START,
    MessagesState,
    StateGraph,
)
from pydantic import PrivateAttr

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
from assistant.llm.client import (
    LLMClient,
    TextDelta,
    ToolCallRequest,
    ToolSpec,
    aclose_iterator,
    to_openai_tools,
)


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

    One adapter covers every provider (fake/openai/ollama/gemini) —
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
        stop: list[str] | None = None,  # noqa: ARG002 — LangChain's signature
        run_manager: AsyncCallbackManagerForLLMRun | None = None,  # noqa: ARG002
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        # LangChain binds tools by pushing OpenAI-format dicts through kwargs.
        tool_dicts: list[dict[str, Any]] = kwargs.get("tools") or []
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
            elif isinstance(event, ToolCallRequest):
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
        max_iterations: int = MAX_ITERATIONS,
    ) -> None:
        self._system_prompt = system_prompt
        self._tools = tools if tools is not None else ToolRegistry()
        self._max_iterations = max_iterations

        model = LLMClientChatModel(llm)
        # Same wire shape the LLM client sends — built by one helper so the
        # two paths cannot describe the same tool differently.
        self._model = (
            model.bind(tools=to_openai_tools(self._tools.specs)) if len(self._tools) else model
        )

        # Per-turn checkpointing: native LangGraph persistence for state
        # inspection, while cross-turn memory stays in shared Redis.
        self._checkpointer = InMemorySaver()
        self._graph = self._build_graph()

    def _build_graph(self):
        async def agent_node(state: MessagesState) -> dict[str, list[BaseMessage]]:
            # Public astream fires token callbacks -> graph stream_mode="messages"
            response: AIMessageChunk | None = None
            async for chunk in self._model.astream(state["messages"]):
                # astream always yields chunks here; cast rather than assert so
                # the narrowing survives `python -O`.
                piece = cast("AIMessageChunk", chunk)
                response = piece if response is None else response + piece
            return {"messages": [response or AIMessageChunk(content="")]}

        async def tools_node(state: MessagesState) -> dict[str, list[BaseMessage]]:
            last = cast("AIMessage", state["messages"][-1])
            results: list[BaseMessage] = []
            for call in last.tool_calls:
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
        builder.add_node("agent", agent_node)  # pyright: ignore[reportUnknownMemberType]
        builder.add_node("tools", tools_node)  # pyright: ignore[reportUnknownMemberType]
        builder.add_edge(START, "agent")
        builder.add_conditional_edges("agent", route_after_agent, {"tools": "tools", END: END})
        builder.add_edge("tools", "agent")
        return builder.compile(checkpointer=self._checkpointer)  # pyright: ignore[reportUnknownMemberType]

    async def run(
        self, history: list[ChatMessage], user_message: str
    ) -> AsyncGenerator[AgentEvent, None]:
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

        thread_id = uuid.uuid4().hex
        config: RunnableConfig = {
            "configurable": {"thread_id": thread_id},
            # agent + tools alternate: 2 super-steps per LLM iteration
            "recursion_limit": 2 * self._max_iterations,
        }

        final_text = ""
        # A list of stream modes makes astream yield (mode, payload) pairs —
        # "messages" carrying (chunk, metadata), "updates" carrying
        # {node: state-delta} — but its declared return type only describes the
        # single-mode case. The cast says what this call actually yields.
        graph_stream = cast(
            "AsyncIterator[tuple[str, Any]]",
            self._graph.astream(  # pyright: ignore[reportUnknownMemberType]
                cast("MessagesState", {"messages": lc_history}),
                config,
                stream_mode=["messages", "updates"],
            ),
        )
        try:
            async for mode, payload in graph_stream:
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
                # {node name: the state delta that node returned}
                for delta in cast("dict[str, Any]", payload).values():
                    updated: list[Any] = delta.get("messages") or []
                    for message in updated:
                        if isinstance(message, AIMessage):
                            if message.tool_calls:
                                for call in message.tool_calls:
                                    yield ToolCallEvent(
                                        tool=call["name"], arguments=dict(call["args"])
                                    )
                            else:
                                final_text = str(message.content)
                        elif isinstance(message, ToolMessage):
                            yield ToolResultEvent(
                                tool=message.name or "unknown",
                                result=truncate_for_event(str(message.content)),
                            )
        except GraphRecursionError:
            yield FinalEvent(content=ITERATION_LIMIT_MESSAGE)
            return
        finally:
            # A stopped turn leaves the graph stream suspended: close it in
            # this task rather than at GC time, then drop the checkpoints —
            # they served their purpose (this turn), and keeping them would
            # make the process grow by one full transcript per turn.
            await aclose_iterator(graph_stream)
            await self._checkpointer.adelete_thread(thread_id)
        yield FinalEvent(content=final_text)
