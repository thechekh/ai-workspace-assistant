"""Provider-agnostic LLM layer.

Every hosted provider here speaks the OpenAI-compatible chat API, so one
client class covers openai/ollama/gemini — the provider is just a
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
    "ollama": "http://localhost:11434/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}

# Retries on 429: provider free tiers use per-minute windows that need longer
# backoff than the SDK's built-in quick retries (which we disable).
RATE_LIMIT_RETRIES = 2
_MAX_RETRY_DELAY_S = 15.0
# Per-request ceiling. Generous enough for a slow first token on a large
# prompt, short enough that a stalled provider cannot hold a turn open.
_REQUEST_TIMEOUT_S = 60.0
# Some providers abort a 200 stream with code "tool_use_failed" when the model
# emits malformed tool-call JSON — a known llama flake, and the reason this
# exists. A fresh attempt usually works. Kept after retiring the provider that
# surfaced it, because it still guards any OpenAI-compatible endpoint serving
# llama models (a local Ollama being the live case).
TOOL_USE_RETRIES = 2


def _retry_after_seconds(exc: RateLimitError) -> float | None:
    header = exc.response.headers.get("retry-after")
    try:
        return float(header) if header else None
    except ValueError:  # HTTP-date form — rare; fall back to our own backoff
        return None


def rate_limit_delay(exc: RateLimitError, attempt: int) -> float:
    """How long to wait before retry `attempt` (1-based) of a 429.

    Shared with the pydantic-ai backend, which drives the provider through its
    own model layer and so cannot reuse the retry loop below — but must not
    grow a second opinion about backoff.
    """
    retry_after = _retry_after_seconds(exc)
    # `retry-after: 0` means "retry now" — a valid value, so test against None
    # rather than truthiness.
    backoff = retry_after if retry_after is not None else 2.0 * attempt
    return min(backoff, _MAX_RETRY_DELAY_S)


def is_tool_use_failure(exc: BaseException) -> bool:
    """A provider's mid-stream "model produced an invalid tool call" error."""
    if getattr(exc, "code", None) == "tool_use_failed":
        return True
    return "failed to call a function" in str(exc).lower()


# llama's native tool syntax, which sometimes leaks into plain text output
# (or arrives in a provider's `failed_generation` field). Observed variants:
# <function.name>{...}</function>, <function=name>{...}, <function(name){...},
# and — seen live on llama-3.1-8b — an opening paren instead of the angle
# bracket: (function=name>{...}. Missing that last one put raw markup in front
# of a user, which is the whole failure this exists to prevent, so the opener
# is matched loosely and the closer stays optional.
# The JSON is brace-matched with raw_decode, so nested arguments parse fine.
_LEAKED_CALL_PREFIX = re.compile(r"[<(]function[.=(]\s*([\w./-]+)\s*[)>]?", re.IGNORECASE)


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


def to_openai_tools(tools: list[ToolSpec]) -> list[ChatCompletionToolParam]:
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


class _LeakedTextBuffer:
    """Holds back leading text while it could still be llama's tool markup.

    llama sometimes prints a tool call as prose (`<function.name>{...}`). We
    cannot know until enough characters arrive, so the opening text is held:
    as soon as it provably is *not* that prefix it is flushed and streaming
    resumes normally. Whatever is still held at the end is parsed as a tool
    call, or emitted as text if it turns out to be ordinary prose.
    """

    # Both openers seen in the wild; the leading character is all that differs.
    _PREFIXES = ("<function", "(function")

    def __init__(self) -> None:
        self._held: list[str] = []
        self._holding = True

    @classmethod
    def _could_be_markup(cls, joined: str) -> bool:
        """Is the text so far still a possible start of a leaked tool call?

        "Does it match the first N characters" alone is not enough: that stays
        true forever once N characters have arrived, so an answer merely
        opening with those letters ("(functional programming…") would be
        withheld until the end of the stream. Real markup always continues with
        a separator, so that is the second thing to require.
        """
        for prefix in cls._PREFIXES:
            if len(joined) < len(prefix):
                if prefix.startswith(joined):
                    return True
            elif joined.startswith(prefix):
                rest = joined[len(prefix) :]
                if not rest or rest[0] in ".=(":
                    return True
        return False

    def push(self, text: str) -> str | None:
        """Returns text to stream now, or None while still withholding."""
        if not self._holding:
            return text
        self._held.append(text)
        joined = "".join(self._held).lstrip()
        if joined and not self._could_be_markup(joined):
            self._holding = False  # ordinary prose — flush everything held
            flushed = "".join(self._held)
            self._held = []
            return flushed
        return None

    def flush(self) -> list[LLMEvent]:
        """Whatever never got streamed: recovered tool calls, or plain text."""
        if not self._held:
            return []
        text = "".join(self._held)
        self._held = []
        leaked = parse_leaked_tool_calls(text)
        if leaked:
            logger.warning("recovered %d tool call(s) from text output", len(leaked))
            return list(leaked)
        return [TextDelta(text=text)]


class _ToolCallAccumulator:
    """Reassembles tool calls whose fragments arrive across many chunks."""

    def __init__(self) -> None:
        self._pending: dict[int, dict[str, str]] = {}

    def add(self, fragment) -> None:
        entry = self._pending.setdefault(fragment.index, {"id": "", "name": "", "arguments": ""})
        if fragment.id:
            entry["id"] = fragment.id
        if fragment.function and fragment.function.name:
            entry["name"] = fragment.function.name
        if fragment.function and fragment.function.arguments:
            entry["arguments"] += fragment.function.arguments

    def finish(self) -> list[ToolCallRequest]:
        return [
            ToolCallRequest(
                id=self._pending[i]["id"],
                name=self._pending[i]["name"],
                arguments=self._pending[i]["arguments"],
            )
            for i in sorted(self._pending)
        ]


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
        """One model step. The body is the retry shell; the two fiddly parts —
        withholding leaked tool markup and reassembling fragmented tool calls —
        live in the helpers above."""
        create_kwargs: dict[str, object] = {
            "model": self.model,
            "messages": _to_openai_messages(messages),
            "stream": True,
            # Ask the provider to report real token usage in the final chunk.
            "stream_options": {"include_usage": True},
        }
        if tools:
            create_kwargs["tools"] = to_openai_tools(tools)

        tool_use_retries = 0
        while True:
            calls = _ToolCallAccumulator()
            buffer = _LeakedTextBuffer()
            usage: UsageEvent | None = None
            emitted = False  # once a delta went downstream, a retry would duplicate output
            try:
                # `async with` so the HTTP response closes deterministically
                # rather than whenever the async generator is collected.
                async with await self._create_stream(create_kwargs) as stream:
                    async for chunk in stream:
                        if chunk.usage is not None:
                            usage = UsageEvent(
                                prompt_tokens=chunk.usage.prompt_tokens or 0,
                                completion_tokens=chunk.usage.completion_tokens or 0,
                            )
                        if not chunk.choices:
                            continue
                        delta = chunk.choices[0].delta
                        if delta.content and (ready := buffer.push(delta.content)) is not None:
                            emitted = True
                            yield TextDelta(text=ready)
                        for fragment in delta.tool_calls or []:
                            calls.add(fragment)
            except APIError as exc:
                if emitted or not is_tool_use_failure(exc):
                    raise
                if tool_use_retries < TOOL_USE_RETRIES:
                    tool_use_retries += 1
                    logger.warning(
                        "model produced an invalid tool call — retrying step (%d/%d)",
                        tool_use_retries,
                        TOOL_USE_RETRIES,
                    )
                    continue
                # Retries exhausted — the provider reports the model's attempt
                # in failed_generation; recover it instead of failing the turn.
                body = exc.body if isinstance(exc.body, dict) else {}
                recovered = parse_leaked_tool_calls(str(body.get("failed_generation") or ""))
                if not recovered:
                    raise
                logger.warning("recovered %d tool call(s) from failed_generation", len(recovered))
                for call in recovered:
                    yield call
                return
            break

        for event in buffer.flush():
            yield event
        for call in calls.finish():
            yield call
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
                if rate_limit_retries >= RATE_LIMIT_RETRIES:
                    raise
                rate_limit_retries += 1
                delay = rate_limit_delay(exc, rate_limit_retries)
                logger.warning(
                    "LLM rate limited (429) — retry %d/%d in %.1fs",
                    rate_limit_retries,
                    RATE_LIMIT_RETRIES,
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
