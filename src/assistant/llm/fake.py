"""Shared offline demo heuristics for the fake providers.

`FakeLLM` (llm/client.py) and the pydantic-ai `FunctionModel` twin
(agent/backends/pydantic_ai.py) must behave **identically** offline —
otherwise the side-by-side backend comparison compares the fakes, not the
backends. They used to carry hand-maintained copies of this routing and had
already drifted (the pydantic-ai copy never learned about `fetch_url`), so
the decision lives here once and each provider only renders the result into
its own event shape.

The heuristics, in priority order:
- mentions of PRs / pull requests -> github__list_pull_requests
- "search code [for] X"           -> code__search_code(pattern=X)
- any http(s) URL in the message  -> fetch_url(url=...)
- a question ending with "?"      -> search_docs(query=question)
- otherwise                       -> a plain echo reply
"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass

_PR_RE = re.compile(r"\bprs?\b")
_URL_RE = re.compile(r"https?://\S+")
_SEARCH_CODE = "search code"


@dataclass(frozen=True)
class FakeToolCall:
    """A tool call the fake provider decided to make."""

    id: str
    name: str
    arguments: str  # JSON string, matching the real ToolCallRequest shape


def stream_words(reply: str) -> Iterator[str]:
    """Split a reply into streaming-sized pieces, preserving spacing."""
    words = reply.split(" ")
    for index, word in enumerate(words):
        yield word if index == 0 else " " + word


def decide_fake_tool_call(user_text: str, tool_names: set[str]) -> FakeToolCall | None:
    """Pick the tool this message should trigger offline, or None to just reply."""
    lowered = user_text.lower()

    if "github__list_pull_requests" in tool_names and (
        "pull request" in lowered or _PR_RE.search(lowered)
    ):
        return FakeToolCall(
            id="call_fake_github", name="github__list_pull_requests", arguments="{}"
        )

    if "code__search_code" in tool_names and _SEARCH_CODE in lowered:
        start = lowered.find(_SEARCH_CODE) + len(_SEARCH_CODE)
        pattern = user_text[start:].strip()
        if pattern.lower().startswith("for "):
            pattern = pattern[4:]
        pattern = pattern.strip(" ?.\"'") or "def "
        return FakeToolCall(
            id="call_fake_code",
            name="code__search_code",
            arguments=json.dumps({"pattern": pattern}),
        )

    if "fetch_url" in tool_names and (url_match := _URL_RE.search(user_text)):
        return FakeToolCall(
            id="call_fake_fetch",
            name="fetch_url",
            arguments=json.dumps({"url": url_match.group(0).rstrip(".,;)?'\"")}),
        )

    if "search_docs" in tool_names and user_text.rstrip().endswith("?"):
        return FakeToolCall(
            id="call_fake_docs",
            name="search_docs",
            arguments=json.dumps({"query": user_text}),
        )

    return None


def tool_result_reply(tool_output: str) -> str:
    """The answer the fake gives once it has seen a tool result."""
    snippet = " ".join(str(tool_output).split())[:400]
    return f"[fake-llm] Based on the tool results: {snippet}"


def echo_reply(message_count: int, user_text: str) -> str:
    """The default reply — reports prompt size so memory tests can assert on it."""
    return f"[fake-llm] ({message_count} messages in context) You said: {user_text}"
