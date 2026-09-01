"""Conversation summarization tests: summarizers + the rolling ConversationMemory."""

from fakeredis import FakeAsyncRedis

from assistant.agent.base import ChatMessage
from assistant.llm.client import TextDelta
from assistant.memory.conversation import SUMMARY_PREFIX, ConversationMemory
from assistant.memory.session import SessionStore
from assistant.memory.summarizer import ExtractiveSummarizer, LLMSummarizer
from tests.conftest import ScriptedLLM


def make_store() -> SessionStore:
    return SessionStore(FakeAsyncRedis(decode_responses=True), ttl_seconds=60)


def turns(count: int, text: str = "some fairly long message body") -> list[ChatMessage]:
    result: list[ChatMessage] = []
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        result.append(ChatMessage(role=role, content=f"{text} {i}"))
    return result


async def test_extractive_summarizer_is_deterministic_and_bounded():
    summarizer = ExtractiveSummarizer()
    messages = turns(20)
    first = await summarizer.summarize("", messages)
    second = await summarizer.summarize("", messages)
    assert first == second
    assert len(first.splitlines()) <= 12
    assert "some fairly long message body 19" in first


async def test_llm_summarizer_collects_streamed_text():
    llm = ScriptedLLM([[TextDelta("Summary: "), TextDelta("they discussed deploys.")]])
    summarizer = LLMSummarizer(llm)
    result = await summarizer.summarize("old summary", turns(2))
    assert result == "Summary: they discussed deploys."


async def test_short_history_passes_through_unchanged():
    store = make_store()
    memory = ConversationMemory(store, ExtractiveSummarizer(), char_budget=10_000, keep_recent=2)
    for message in turns(4):
        await store.append("s1", message)

    context = await memory.context_for("s1")
    assert len(context) == 4
    assert all(message.role != "system" for message in context)


async def test_over_budget_history_folds_into_summary():
    store = make_store()
    memory = ConversationMemory(store, ExtractiveSummarizer(), char_budget=100, keep_recent=2)
    for message in turns(6):
        await store.append("s1", message)

    context = await memory.context_for("s1")
    # summary + the 2 most recent verbatim messages
    assert len(context) == 3
    assert context[0].role == "system"
    assert context[0].content.startswith(SUMMARY_PREFIX)
    assert context[1].content.endswith("4")
    assert context[2].content.endswith("5")

    # the fold is persisted: covered index advanced, summary stored
    summary, covered = await store.summary("s1")
    assert covered == 4
    assert "some fairly long message body 0" in summary


async def test_folding_is_incremental_across_calls():
    store = make_store()
    memory = ConversationMemory(store, ExtractiveSummarizer(), char_budget=100, keep_recent=2)
    for message in turns(6):
        await store.append("s1", message)
    await memory.context_for("s1")
    _, covered_first = await store.summary("s1")

    # two more turns arrive -> only the new overflow is folded, not everything again
    for message in turns(2, text="fresh turn with plenty of characters inside"):
        await store.append("s1", message)
    context = await memory.context_for("s1")
    _, covered_second = await store.summary("s1")

    assert covered_second > covered_first
    assert len(context) == 3  # summary + keep_recent
