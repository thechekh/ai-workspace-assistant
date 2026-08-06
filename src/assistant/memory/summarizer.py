"""Rolling-summary strategies for long conversations.

`ExtractiveSummarizer` is deterministic and offline (the `fake` provider /
test default); `LLMSummarizer` asks the configured model to maintain the
running summary (the production path — use a cheap model).
"""

from typing import Protocol

from assistant.agent.base import ChatMessage
from assistant.config import Settings
from assistant.llm.client import LLMClient, TextDelta

_MAX_SUMMARY_LINES = 12
_LINE_SNIPPET_CHARS = 100


class Summarizer(Protocol):
    async def summarize(self, previous_summary: str, messages: list[ChatMessage]) -> str:
        """Fold `messages` into `previous_summary`, returning the new summary."""
        ...


class ExtractiveSummarizer:
    """Deterministic, zero-cost: keeps the last N one-line digests of the turns."""

    async def summarize(self, previous_summary: str, messages: list[ChatMessage]) -> str:
        lines = [line for line in previous_summary.splitlines() if line.strip()]
        for message in messages:
            snippet = " ".join(message.content.split())[:_LINE_SNIPPET_CHARS]
            lines.append(f"- {message.role}: {snippet}")
        return "\n".join(lines[-_MAX_SUMMARY_LINES:])


class LLMSummarizer:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def summarize(self, previous_summary: str, messages: list[ChatMessage]) -> str:
        transcript = "\n".join(f"{message.role}: {message.content}" for message in messages)
        prompt = (
            "Update the running summary of this conversation. Keep every fact, "
            "decision, name, and number that could matter later; drop pleasantries. "
            "Reply with the updated summary only, at most 200 words.\n\n"
            f"Current summary:\n{previous_summary or '(empty)'}\n\n"
            f"New turns to fold in:\n{transcript}"
        )
        request = [
            ChatMessage(role="system", content="You maintain factual conversation summaries."),
            ChatMessage(role="user", content=prompt),
        ]
        parts = [
            event.text
            async for event in self._llm.stream_step(request)
            if isinstance(event, TextDelta)
        ]
        return "".join(parts).strip()


def build_summarizer(settings: Settings, llm: LLMClient) -> Summarizer:
    if settings.llm_provider == "fake":
        return ExtractiveSummarizer()
    return LLMSummarizer(llm)
