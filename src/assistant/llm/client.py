"""Provider-agnostic LLM layer.

Every hosted provider here speaks the OpenAI-compatible chat API, so one
client class covers groq/ollama/gemini/openai — the provider is just a
base_url + key. `FakeLLM` is the offline deterministic provider used as the
dev/test default (zero cost, no network).

An LLM *step* streams `TextDelta` events and may end with one or more
`ToolCallRequest` events — the agent loop executes those and calls the next
step with the results appended.
"""

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, cast

import httpx
from openai import APIError, AsyncOpenAI, AsyncStream, BadRequestError, RateLimitError
from openai.types.chat import (
    ChatCompletionChunk,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
)

from assistant.agent.base import ChatMessage
from assistant.config import Settings
from assistant.llm.fake import decide_fake_tool_call, echo_reply, stream_words, tool_result_reply

logger = logging.getLogger(__name__)

PROVIDER_BASE_URLS: dict[str, str | None] = {
    "openai": None,  # SDK default
    "groq": "https://api.groq.com/openai/v1",
    "ollama": "http://localhost:11434/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}

# Retries on 429: free tiers (Groq: ~30 req/min) use per-minute windows that
# need longer backoff than the SDK's built-in quick retries (which we disable).
_RATE_LIMIT_RETRIES = 2
_MAX_RETRY_DELAY_S = 15.0
# Per-request ceiling. Generous enough for a slow first token on a large
# prompt, short enough that a stalled provider cannot hold a turn open.
_REQUEST_TIMEOUT_S = 60.0
# Groq aborts a 200 stream with code "tool_use_failed" when the model emits
# malformed tool-call JSON (a known llama flake) — a fresh attempt usually works.
_TOOL_USE_RETRIES = 2


def _retry_after_seconds(exc: RateLimitError) -> float | None:
    header = exc.response.headers.get("retry-after")
    try:
        return float(header) if header else None
    except ValueError:  # HTTP-date form — rare; fall back to our own backoff
        return None


def is_tool_use_failure(exc: BaseException) -> bool:
    """Groq's mid-stream 'model produced an invalid tool call' error."""
    if getattr(exc, "code", None) == "tool_use_failed":
        return True
    return "failed to call a function" in str(exc).lower()


# llama's native tool syntax, which sometimes leaks into plain text output
# (or arrives via Groq's `failed_generation`). Observed variants:
# <function.name>{...}</function>, <function=name>{...}, <function(name){...}.
# The JSON is brace-matched with raw_decode, so nested arguments parse fine.
_LEAKED_CALL_PREFIX = re.compile(r"<function[.=(]\s*([\w./-]+)\s*[)>]?", re.IGNORECASE)


@dataclass(frozen=True)
class ToolSpec:
    """What the model sees: a function name, description, and JSON schema."""

    name: str
    description: str
    parameters: dict[str, object]


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCallRequest:
    id: str
    name: str
    arguments: str  # raw JSON string from the model


@dataclass(frozen=True)
class UsageEvent:
    """Real token usage reported by the provider (final stream chunk).

    Emitted by OpenAICompatibleLLM and consumed by telemetry.InstrumentedLLM —
    agent loops never see it (and defensively ignore unknown events anyway).
    """

    prompt_tokens: int
    completion_tokens: int


LLMEvent = TextDelta | ToolCallRequest | UsageEvent


def parse_leaked_tool_calls(text: str) -> list[ToolCallRequest] | None:
    """Recover tool calls that a llama model emitted as *text* instead of via
    the tool_calls API — the `<function...>` markup, or a bare
    {"name": ..., "arguments": ...} object. Returns None when the text does
    not parse as tool calls (callers then treat it as a normal answer)."""
    decoder = json.JSONDecoder()
    calls: list[ToolCallRequest] = []
    position = 0
    while match := _LEAKED_CALL_PREFIX.search(text, position):
        start = text.find("{", match.end())
        if start == -1:
            break
        try:
            payload, end = decoder.raw_decode(text, start)
        except ValueError:
            position = match.end()
            continue
        calls.append(
            ToolCallRequest(
                id=f"call_recovered_{len(calls)}",
                name=match.group(1),
                arguments=json.dumps(payload),
            )
        )
        position = end
    if calls:
        return calls

    # Bare-JSON form: one object naming the function directly.
    try:
        payload = json.loads(text.strip())
    except ValueError:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("name"), str):
        arguments = payload.get("arguments") or payload.get("parameters") or {}
        return [
            ToolCallRequest(
                id="call_recovered_0", name=payload["name"], arguments=json.dumps(arguments)
            )
        ]
    return None


class LLMClient(Protocol):
    def stream_step(
        self, messages: list[ChatMessage], tools: list[ToolSpec] | None = None
    ) -> AsyncIterator[LLMEvent]:
        """Stream one model step: text deltas, then any requested tool calls."""
        ...


def _to_openai_messages(messages: list[ChatMessage]) -> list[ChatCompletionMessageParam]:
    payload: list[dict[str, object]] = []
    for message in messages:
        if message.role == "tool":
            payload.append(
                {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
            )
        elif message.role == "assistant" and message.tool_calls:
            payload.append(
                {
                    "role": "assistant",
                    "content": message.content or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments},
                        }
                        for call in message.tool_calls
                    ],
                }
            )
        else:
            payload.append({"role": message.role, "content": message.content})
    return cast("list[ChatCompletionMessageParam]", payload)


def _to_openai_tools(tools: list[ToolSpec]) -> list[ChatCompletionToolParam]:
    return cast(
        "list[ChatCompletionToolParam]",
        [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ],
    )


class OpenAICompatibleLLM:
    def __init__(self, model: str, api_key: str, base_url: str | None) -> None:
        self.model = model
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            # The SDK defaults to a 600s read timeout, which would pin a chat
            # turn for ten minutes on a stalled provider. Its own retries are
            # disabled because this class hand-rolls them below (otherwise the
            # two multiply).
            timeout=httpx.Timeout(_REQUEST_TIMEOUT_S, connect=5.0),
            max_retries=0,
        )

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._client.close()

    async def stream_step(
        self, messages: list[ChatMessage], tools: list[ToolSpec] | None = None
    ) -> AsyncIterator[LLMEvent]:
        payload = _to_openai_messages(messages)
        create_kwargs: dict[str, object] = {
            "model": self.model,
            "messages": payload,
            "stream": True,
            # Ask the provider to report real token usage in the final chunk.
            "stream_options": {"include_usage": True},
        }
        if tools:
            create_kwargs["tools"] = _to_openai_tools(tools)

        # Tool-call fragments arrive interleaved across chunks; accumulate by index.
        pending: dict[int, dict[str, str]] = {}
        usage: UsageEvent | None = None
        held: list[str] = []  # text withheld while it still looks like leaked tool syntax
        tool_use_retries = 0
        while True:
            stream = await self._create_stream(create_kwargs)
            pending = {}
            usage = None
            held = []
            holding = True  # hold text until it provably isn't a leaked "<function..." call
            emitted = False  # once a delta went downstream, a retry would duplicate output
            try:
                async for chunk in stream:
                    if chunk.usage is not None:
                        usage = UsageEvent(
                            prompt_tokens=chunk.usage.prompt_tokens or 0,
                            completion_tokens=chunk.usage.completion_tokens or 0,
                        )
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        if holding:
                            held.append(delta.content)
                            joined = "".join(held).lstrip()
                            prefix = joined[: len("<function")]
                            if joined and not "<function".startswith(prefix):
                                holding = False  # ordinary prose — flush and stream on
                                emitted = True
                                yield TextDelta(text="".join(held))
                                held = []
                        else:
                            emitted = True
                            yield TextDelta(text=delta.content)
                    for fragment in delta.tool_calls or []:
                        entry = pending.setdefault(
                            fragment.index, {"id": "", "name": "", "arguments": ""}
                        )
                        if fragment.id:
                            entry["id"] = fragment.id
                        if fragment.function and fragment.function.name:
                            entry["name"] = fragment.function.name
                        if fragment.function and fragment.function.arguments:
                            entry["arguments"] += fragment.function.arguments
            except APIError as exc:
                if emitted or not is_tool_use_failure(exc):
                    raise
                if tool_use_retries < _TOOL_USE_RETRIES:
                    tool_use_retries += 1
                    logger.warning(
                        "model produced an invalid tool call — retrying step (%d/%d)",
                        tool_use_retries,
                        _TOOL_USE_RETRIES,
                    )
                    continue
                # Retries exhausted — Groq reports the model's attempt in
                # failed_generation; recover the call instead of failing the turn.
                body = exc.body if isinstance(exc.body, dict) else {}
                recovered = parse_leaked_tool_calls(str(body.get("failed_generation") or ""))
                if not recovered:
                    raise
                logger.warning("recovered %d tool call(s) from failed_generation", len(recovered))
                for call in recovered:
                    yield call
                return
            break

        if held:
            # The whole answer looked like llama's tool markup: parse it into
            # real tool calls, or flush it as text if it turns out to be prose.
            leaked = parse_leaked_tool_calls("".join(held))
            if leaked:
                logger.warning("recovered %d tool call(s) from text output", len(leaked))
                for call in leaked:
                    yield call
            else:
                yield TextDelta(text="".join(held))
        for index in sorted(pending):
            entry = pending[index]
            yield ToolCallRequest(id=entry["id"], name=entry["name"], arguments=entry["arguments"])
        if usage is not None:
            yield usage

    async def _create_stream(
        self, create_kwargs: dict[str, object]
    ) -> AsyncStream[ChatCompletionChunk]:
        """Create the completion stream, absorbing two provider quirks:
        reject-retry when stream_options is unsupported, and honored-backoff
        retries on 429 (Retry-After when given, capped at 15s per wait)."""
        rate_limit_retries = 0
        while True:
            try:
                return await self._client.chat.completions.create(**create_kwargs)  # type: ignore[arg-type]
            except BadRequestError:
                if "stream_options" not in create_kwargs:
                    raise
                # Some OpenAI-compatible providers reject stream_options — retry without.
                create_kwargs.pop("stream_options", None)
            except RateLimitError as exc:
                if rate_limit_retries >= _RATE_LIMIT_RETRIES:
                    raise
                rate_limit_retries += 1
                # `retry-after: 0` means "retry now" — it is a valid value, so
                # test it against None rather than truthiness (0.0 is falsy).
                retry_after = _retry_after_seconds(exc)
                backoff = retry_after if retry_after is not None else 2.0 * rate_limit_retries
                delay = min(backoff, _MAX_RETRY_DELAY_S)
                logger.warning(
                    "LLM rate limited (429) — retry %d/%d in %.1fs",
                    rate_limit_retries,
                    _RATE_LIMIT_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)


class FakeLLM:
    """Deterministic offline provider for tests and first-run development.

    Plain messages are echoed back (reporting prompt size, so tests can assert
    that session memory works). With tools offered, it plays a one-round agent
    on the shared heuristics in `llm.fake` — the same ones the pydantic-ai
    FunctionModel twin uses, so all backends behave identically offline.
    """

    async def stream_step(
        self, messages: list[ChatMessage], tools: list[ToolSpec] | None = None
    ) -> AsyncIterator[LLMEvent]:
        last = messages[-1] if messages else ChatMessage(role="user", content="")
        tool_names = {tool.name for tool in tools or []}

        if last.role == "tool":
            for piece in stream_words(tool_result_reply(last.content)):
                yield TextDelta(text=piece)
            return

        if last.role == "user" and tool_names:
            call = decide_fake_tool_call(last.content, tool_names)
            if call is not None:
                yield ToolCallRequest(id=call.id, name=call.name, arguments=call.arguments)
                return

        for piece in stream_words(echo_reply(len(messages), last.content)):
            yield TextDelta(text=piece)


def resolve_provider(settings: Settings) -> tuple[str, str | None]:
    """Credentials for a hosted provider: (api_key, base_url).

    Shared by `build_llm` and the pydantic-ai model factory so adding a
    provider or changing key handling is a single edit.
    """
    provider = settings.llm_provider
    api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
    if provider == "ollama":
        api_key = api_key or "ollama"  # local Ollama ignores the key
    if api_key is None:
        raise ValueError(f"ASSISTANT_LLM_API_KEY is required for provider {provider!r}")
    return api_key, settings.llm_base_url or PROVIDER_BASE_URLS[provider]


def build_llm(settings: Settings) -> LLMClient:
    if settings.llm_provider == "fake":
        return FakeLLM()
    api_key, base_url = resolve_provider(settings)
    return OpenAICompatibleLLM(model=settings.llm_model, api_key=api_key, base_url=base_url)
