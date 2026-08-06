"""Provider-agnostic LLM layer.

Every hosted provider here speaks the OpenAI-compatible chat API, so one
client class covers groq/ollama/gemini/openai — the provider is just a
base_url + key. `FakeLLM` is the offline deterministic provider used as the
dev/test default (zero cost, no network).

An LLM *step* streams `TextDelta` events and may end with one or more
`ToolCallRequest` events — the agent loop executes those and calls the next
step with the results appended.
"""

import json
import re
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Protocol, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

from assistant.agent.base import ChatMessage
from assistant.config import Settings

PROVIDER_BASE_URLS: dict[str, str | None] = {
    "openai": None,  # SDK default
    "groq": "https://api.groq.com/openai/v1",
    "ollama": "http://localhost:11434/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}


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


LLMEvent = TextDelta | ToolCallRequest


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
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def stream_step(
        self, messages: list[ChatMessage], tools: list[ToolSpec] | None = None
    ) -> AsyncIterator[LLMEvent]:
        if tools:
            stream = await self._client.chat.completions.create(
                model=self.model,
                messages=_to_openai_messages(messages),
                tools=_to_openai_tools(tools),
                stream=True,
            )
        else:
            stream = await self._client.chat.completions.create(
                model=self.model,
                messages=_to_openai_messages(messages),
                stream=True,
            )
        # Tool-call fragments arrive interleaved across chunks; accumulate by index.
        pending: dict[int, dict[str, str]] = {}
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield TextDelta(text=delta.content)
            for fragment in delta.tool_calls or []:
                entry = pending.setdefault(fragment.index, {"id": "", "name": "", "arguments": ""})
                if fragment.id:
                    entry["id"] = fragment.id
                if fragment.function and fragment.function.name:
                    entry["name"] = fragment.function.name
                if fragment.function and fragment.function.arguments:
                    entry["arguments"] += fragment.function.arguments
        for index in sorted(pending):
            entry = pending[index]
            yield ToolCallRequest(id=entry["id"], name=entry["name"], arguments=entry["arguments"])


def _stream_words(reply: str) -> Iterator[str]:
    words = reply.split(" ")
    for i, word in enumerate(words):
        yield word if i == 0 else " " + word


class FakeLLM:
    """Deterministic offline provider for tests and first-run development.

    Plain messages are echoed back (reporting prompt size, so tests can assert
    that session memory works). With tools offered, it plays a one-round agent
    on simple keyword heuristics so the whole loop demos offline at zero cost:

    - mentions of PRs / pull requests -> github__list_pull_requests
    - "search code [for] X"           -> code__search_code(pattern=X)
    - a question ending with "?"      -> search_docs(query=question)
    - after any tool result           -> answer quoting the result
    """

    async def stream_step(
        self, messages: list[ChatMessage], tools: list[ToolSpec] | None = None
    ) -> AsyncIterator[LLMEvent]:
        last = messages[-1] if messages else ChatMessage(role="user", content="")
        tool_names = {tool.name for tool in tools or []}

        if last.role == "tool":
            snippet = " ".join(last.content.split())[:400]
            reply = f"[fake-llm] Based on the tool results: {snippet}"
            for piece in _stream_words(reply):
                yield TextDelta(text=piece)
            return

        if last.role == "user" and tool_names:
            lowered = last.content.lower()

            if "github__list_pull_requests" in tool_names and (
                "pull request" in lowered or re.search(r"\bprs?\b", lowered)
            ):
                yield ToolCallRequest(
                    id="call_fake_github", name="github__list_pull_requests", arguments="{}"
                )
                return

            if "code__search_code" in tool_names and "search code" in lowered:
                start = lowered.find("search code") + len("search code")
                pattern = last.content[start:].strip()
                if pattern.lower().startswith("for "):
                    pattern = pattern[4:]
                pattern = pattern.strip(" ?.\"'") or "def "
                yield ToolCallRequest(
                    id="call_fake_code",
                    name="code__search_code",
                    arguments=json.dumps({"pattern": pattern}),
                )
                return

            if "search_docs" in tool_names and last.content.rstrip().endswith("?"):
                yield ToolCallRequest(
                    id="call_fake_docs",
                    name="search_docs",
                    arguments=json.dumps({"query": last.content}),
                )
                return

        reply = f"[fake-llm] ({len(messages)} messages in context) You said: {last.content}"
        for piece in _stream_words(reply):
            yield TextDelta(text=piece)


def build_llm(settings: Settings) -> LLMClient:
    provider = settings.llm_provider
    if provider == "fake":
        return FakeLLM()

    api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
    if provider == "ollama":
        api_key = api_key or "ollama"  # local Ollama ignores the key
    if api_key is None:
        raise ValueError(f"ASSISTANT_LLM_API_KEY is required for provider {provider!r}")

    base_url = settings.llm_base_url or PROVIDER_BASE_URLS[provider]
    return OpenAICompatibleLLM(model=settings.llm_model, api_key=api_key, base_url=base_url)
