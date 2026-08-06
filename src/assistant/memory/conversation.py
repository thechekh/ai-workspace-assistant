"""ConversationMemory: rolling summarization over the Redis-backed history.

The full transcript stays in Redis untouched (audit trail). What the agent
sees is bounded: once the un-summarized tail exceeds the char budget, older
turns are folded into a persisted rolling summary and only the most recent
messages remain verbatim:

    context = [summary (as a system message)] + recent turns

The summary is stored per session, so folding work is incremental — each
message is summarized at most once.
"""

from assistant.agent.base import ChatMessage
from assistant.memory.session import SessionStore
from assistant.memory.summarizer import Summarizer

SUMMARY_PREFIX = "Summary of the earlier conversation:"


class ConversationMemory:
    def __init__(
        self,
        store: SessionStore,
        summarizer: Summarizer,
        *,
        char_budget: int,
        keep_recent: int,
    ) -> None:
        self._store = store
        self._summarizer = summarizer
        self._char_budget = char_budget
        self._keep_recent = keep_recent

    async def context_for(self, session_id: str) -> list[ChatMessage]:
        """History as the agent should see it: bounded, summary-first."""
        history = await self._store.history(session_id)
        summary, covered = await self._store.summary(session_id)
        pending = history[covered:]

        pending_chars = sum(len(message.content) for message in pending)
        if pending_chars > self._char_budget and len(pending) > self._keep_recent:
            fold = pending[: -self._keep_recent]
            summary = await self._summarizer.summarize(summary, fold)
            covered += len(fold)
            await self._store.set_summary(session_id, summary, covered)
            pending = pending[-self._keep_recent :]

        context: list[ChatMessage] = []
        if summary:
            context.append(ChatMessage(role="system", content=f"{SUMMARY_PREFIX}\n{summary}"))
        context.extend(pending)
        return context
