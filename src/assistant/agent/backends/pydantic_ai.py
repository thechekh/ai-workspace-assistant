"""Backend B — Pydantic AI.

Implements the same AgentBackend contract as the custom loop, driven by
pydantic-ai's graph iteration API (`agent.iter`). Tools come from the shared
ToolRegistry (including the MCP-adapted ones) via `Tool.from_schema`, so all
backends expose identical capabilities — deliberately, for an apples-to-apples
comparison. (pydantic-ai also has native MCP client support; we route through
the shared registry instead so the three backends stay interchangeable.)

The `fake` LLM provider maps to a pydantic-ai FunctionModel that mirrors
FakeLLM's demo heuristics — the whole backend runs offline at zero cost.

One asymmetry is worth knowing about: this backend drives the provider through
pydantic-ai's own model layer, so it does *not* inherit the hardening in
`llm/client.py` (429 backoff, `failed_generation` salvage, leaked-`<function>`
parsing). The retry below closes the gap that actually bit — a provider aborting a
stream with `tool_use_failed` — because without it this backend answered a
knowledge-base question with an error while the other two retried and
recovered, which is precisely the parity the project claims.
"""

import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator, AsyncIterator

import structlog
from openai import RateLimitError
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.exceptions import UsageLimitExceeded
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
from pydantic_ai.usage import UsageLimits

from assistant.agent.base import (
    ITERATION_LIMIT_MESSAGE,
    MAX_ITERATIONS,
    AgentEvent,
    ChatMessage,
    FinalEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    truncate_for_event,
)
from assistant.agent.tools import Tool as RegistryTool
from assistant.agent.tools import ToolRegistry
from assistant.config import Settings
from assistant.llm.client import (
    RATE_LIMIT_RETRIES,
    TOOL_USE_RETRIES,
    is_tool_use_failure,
    rate_limit_delay,
    resolve_provider,
)
from assistant.llm.fake import decide_fake_tool_call, echo_reply, stream_words, tool_result_reply
from assistant.telemetry import record_external_usage

logger = structlog.get_logger("assistant.agent.pydantic_ai")


def _adapt_tool(tool: RegistryTool) -> PydanticTool:
    """Wrap a registry tool (JSON schema + async handler) as a pydantic-ai Tool."""

    async def call(**kwargs: object) -> str:
        return await tool.run(dict(kwargs))  # Tool.run = the telemetry seam

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
        self,
        model: Model | str,
        system_prompt: str,
        tools: ToolRegistry | None = None,
        max_iterations: int = MAX_ITERATIONS,
    ) -> None:
        self._system_prompt = system_prompt
        # FunctionModel (the fake provider) estimates its token counts; a real
        # provider reports them, and the stats line shows the difference.
        self._usage_is_estimate = isinstance(model, FunctionModel)
        # The same cap the other two backends enforce, expressed as pydantic-ai's
        # request limit: one model request per iteration. Without it the
        # framework default (50 requests) applied, and a looping model cost
        # eight times more on this backend than on the other two.
        self._usage_limits = UsageLimits(request_limit=max_iterations)
        self._agent = PydanticAgent(
            model,
            system_prompt=system_prompt,
            tools=[_adapt_tool(tool) for tool in (tools.tools if tools else [])],
        )

    async def run(
        self, history: list[ChatMessage], user_message: str
    ) -> AsyncGenerator[AgentEvent, None]:
        """One turn, retrying a provider-side malformed tool call.

        This backend drives the provider through pydantic-ai's model layer, so
        it never passes through `OpenAICompatibleLLM` and inherits none of its
        retries. Two provider behaviours make that visible in normal use: a 429
        on a free tier, and a model emitting tool-call JSON the provider
        rejects (the stream aborts with `tool_use_failed`). Both were observed
        turning a perfectly good question into an error here while the other
        two backends recovered — the exact parity this project claims.

        Retrying is only safe until the first event reaches the caller: after
        that a second attempt would duplicate what is already on screen. That
        is the same `emitted` guard `OpenAICompatibleLLM.stream_step` uses, and
        the backoff comes from the same function, so there is one opinion about
        how long to wait rather than two.
        """
        tool_use_retries = 0
        rate_limit_retries = 0
        while True:
            emitted = False
            attempt = self._run_once(history, user_message)
            try:
                async for event in attempt:
                    emitted = True
                    yield event
                return
            except RateLimitError as exc:
                if emitted or rate_limit_retries >= RATE_LIMIT_RETRIES:
                    raise
                rate_limit_retries += 1
                delay = rate_limit_delay(exc, rate_limit_retries)
                logger.warning(
                    "llm.rate_limited",
                    attempt=rate_limit_retries,
                    retries=RATE_LIMIT_RETRIES,
                    delay_s=round(delay, 1),
                )
                await asyncio.sleep(delay)
            except Exception as exc:
                if emitted or tool_use_retries >= TOOL_USE_RETRIES or not is_tool_use_failure(exc):
                    raise
                tool_use_retries += 1
                logger.warning(
                    "llm.invalid_tool_call_retry",
                    attempt=tool_use_retries,
                    retries=TOOL_USE_RETRIES,
                )
            finally:
                # Close the inner run in *this* task. A stopped turn closes
                # this generator, and leaving the inner one to the garbage
                # collector meant pydantic-ai's cancel scopes were exited from
                # whatever task finalized it — "Attempted to exit cancel scope
                # in a different task than it was entered in".
                await attempt.aclose()

    async def _run_once(
        self, history: list[ChatMessage], user_message: str
    ) -> AsyncGenerator[AgentEvent, None]:
        """One attempt: the framework runs in a task of its own; this relays.

        `agent.iter()` and `node.stream()` are async context managers built
        on anyio cancel scopes, which must be exited by the task that entered
        them. Yielding from inside them, and letting a consumer that stops
        early close this generator, broke that rule: the scopes were exited
        from whatever task finalized the generator ("Attempted to exit cancel
        scope in a different task than it was entered in"), or the close
        collided with a tool still running. Cancelling a task is the one exit
        the framework does support, so that is what stopping early does.
        """
        queue: asyncio.Queue[AgentEvent | BaseException | None] = asyncio.Queue()
        producer = asyncio.create_task(self._produce(history, user_message, queue))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            if not producer.done():
                producer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await producer

    async def _produce(
        self,
        history: list[ChatMessage],
        user_message: str,
        queue: asyncio.Queue[AgentEvent | BaseException | None],
    ) -> None:
        """Drive pydantic-ai and hand every event (or the failure) to the queue."""
        message_history = _to_model_messages(self._system_prompt, history) if history else None
        llm_ms = 0.0
        run = None
        try:
            async with self._agent.iter(
                user_message, message_history=message_history, usage_limits=self._usage_limits
            ) as agent_run:
                run = agent_run
                async for node in agent_run:
                    if PydanticAgent.is_model_request_node(node):
                        started = time.perf_counter()
                        async with node.stream(agent_run.ctx) as request_stream:
                            async for event in request_stream:
                                if (
                                    isinstance(event, PartStartEvent)
                                    and isinstance(event.part, TextPart)
                                    and event.part.content
                                ):
                                    queue.put_nowait(TokenEvent(content=event.part.content))
                                elif (
                                    isinstance(event, PartDeltaEvent)
                                    and isinstance(event.delta, TextPartDelta)
                                    and event.delta.content_delta
                                ):
                                    queue.put_nowait(TokenEvent(content=event.delta.content_delta))
                        llm_ms += (time.perf_counter() - started) * 1000
                    elif PydanticAgent.is_call_tools_node(node):
                        async with node.stream(agent_run.ctx) as tools_stream:
                            async for event in tools_stream:
                                if isinstance(event, FunctionToolCallEvent):
                                    queue.put_nowait(
                                        ToolCallEvent(
                                            tool=event.part.tool_name,
                                            arguments=event.part.args_as_dict(),
                                        )
                                    )
                                elif isinstance(event, FunctionToolResultEvent):
                                    # pydantic-ai 2.x renamed `.result` to `.part`
                                    # (inherited from the ToolResultEvent base).
                                    part = event.part
                                    text = (
                                        str(part.content)
                                        if isinstance(part, ToolReturnPart)
                                        else f"error: {part.model_response()}"
                                    )
                                    queue.put_nowait(
                                        ToolResultEvent(
                                            tool=part.tool_name or "unknown",
                                            result=truncate_for_event(text),
                                        )
                                    )
            output = run.result.output if run.result is not None else ""
            queue.put_nowait(FinalEvent(content=output if isinstance(output, str) else str(output)))
        except UsageLimitExceeded:
            # The same ending the other two backends give a looping model.
            queue.put_nowait(FinalEvent(content=ITERATION_LIMIT_MESSAGE))
        except asyncio.CancelledError:
            raise  # the consumer stopped early; nothing to relay
        except BaseException as exc:  # noqa: BLE001 — relayed, re-raised in the consumer's task
            queue.put_nowait(exc)
        finally:
            # This backend bypasses InstrumentedLLM, so it reports the run's
            # usage itself — otherwise the stats line, the cost and the token
            # counters read near zero for one backend out of three. In a
            # `finally`, like InstrumentedLLM: a stopped or failed turn still
            # spent its tokens, and that is exactly when the cost must show.
            if run is not None:
                usage = run.usage  # a property in pydantic-ai 2.x
                record_external_usage(
                    prompt_tokens=usage.input_tokens or 0,
                    completion_tokens=usage.output_tokens or 0,
                    steps=usage.requests or 0,
                    llm_ms=llm_ms,
                    estimated=self._usage_is_estimate,
                )
            queue.put_nowait(None)


async def _fake_stream(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    """FunctionModel twin of FakeLLM.

    Both share `llm.fake` for the routing decision — only the rendering into
    pydantic-ai's delta shape differs — so the two can no longer drift.
    """
    tool_names = {tool.name for tool in info.function_tools}
    last = messages[-1]
    last_part = last.parts[-1] if last.parts else None

    if isinstance(last_part, ToolReturnPart):
        for piece in stream_words(tool_result_reply(str(last_part.content))):
            yield piece
        return

    user_text = ""
    for part in reversed(last.parts):
        if isinstance(part, UserPromptPart):
            user_text = str(part.content)
            break

    call = decide_fake_tool_call(user_text, tool_names)
    if call is not None:
        yield {0: DeltaToolCall(name=call.name, json_args=call.arguments)}
        return

    total_parts = sum(len(message.parts) for message in messages)
    for piece in stream_words(echo_reply(total_parts, user_text)):
        yield piece


def build_pydantic_model(settings: Settings) -> Model:
    """Settings -> pydantic-ai model, sharing llm.client's provider resolution."""
    if settings.llm_provider == "fake":
        return FunctionModel(stream_function=_fake_stream, model_name="fake-llm")

    api_key, base_url = resolve_provider(settings)
    openai_provider = (
        OpenAIProvider(base_url=base_url, api_key=api_key)
        if base_url is not None
        else OpenAIProvider(api_key=api_key)
    )
    return OpenAIChatModel(settings.llm_model, provider=openai_provider)
