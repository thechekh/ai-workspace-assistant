from assistant.agent.backends.custom import CustomAgent
from assistant.agent.base import ChatMessage, FinalEvent, TokenEvent
from assistant.llm.client import FakeLLM


async def test_custom_agent_streams_then_finalizes():
    agent = CustomAgent(llm=FakeLLM(), system_prompt="system prompt")
    events = [event async for event in agent.run(history=[], user_message="hello")]

    assert isinstance(events[-1], FinalEvent)
    tokens = [e.content for e in events if isinstance(e, TokenEvent)]
    assert "".join(tokens) == events[-1].content
    assert "You said: hello" in events[-1].content


async def test_custom_agent_includes_history_in_prompt():
    agent = CustomAgent(llm=FakeLLM(), system_prompt="system prompt")
    history = [
        ChatMessage(role="user", content="earlier question"),
        ChatMessage(role="assistant", content="earlier answer"),
    ]
    events = [event async for event in agent.run(history=history, user_message="follow-up")]

    final = events[-1]
    assert isinstance(final, FinalEvent)
    # system + 2 history + 1 user = 4 messages reached the LLM
    assert "(4 messages in context)" in final.content
