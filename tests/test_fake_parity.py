"""All three backends must behave identically offline.

The fake providers exist so the whole agent loop demos with zero cost — and
so the backend comparison compares *backends*, not their fakes. These tests
run the same prompts through every runtime end-to-end and assert the same
tool gets called.

Regression guard: the pydantic-ai fake once carried a hand-copied version of
these heuristics and silently missed `fetch_url`, so offline it answered
URL questions without ever fetching. Both fakes now share `llm.fake`.
"""

import json

import pytest
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient

from assistant.agent.base import ChatMessage
from assistant.config import AgentBackendName
from assistant.llm.client import FakeLLM, ToolCallRequest, ToolSpec
from assistant.llm.fake import decide_fake_tool_call
from assistant.main import create_app
from tests.conftest import HermeticSettings, build_seeded_retriever, collect_until_final

BACKENDS: list[AgentBackendName] = ["custom", "pydantic_ai", "langgraph"]

# (prompt, expected tool). These are the documented offline demo triggers.
ROUTING_CASES = [
    ("Show me the latest PRs", "github__list_pull_requests"),
    ("search code for class CustomAgent", "code__search_code"),
    ("what is https://github.com/thechekh/awsomequiz-streamlit about?", "fetch_url"),
    ("Which service generates PDF invoices?", "search_docs"),
]

ALL_TOOLS = {"github__list_pull_requests", "code__search_code", "fetch_url", "search_docs"}


# --- the shared decision function -------------------------------------------


@pytest.mark.parametrize(("prompt", "expected_tool"), ROUTING_CASES)
def test_decide_fake_tool_call_routes_each_trigger(prompt: str, expected_tool: str):
    call = decide_fake_tool_call(prompt, ALL_TOOLS)
    assert call is not None
    assert call.name == expected_tool
    json.loads(call.arguments)  # arguments must always be valid JSON


def test_decide_fake_tool_call_returns_none_for_plain_chat():
    assert decide_fake_tool_call("hello there", ALL_TOOLS) is None


def test_decide_fake_tool_call_respects_available_tools():
    # A trigger whose tool is not offered must not be invented.
    assert decide_fake_tool_call("Show me the latest PRs", {"search_docs"}) is None


def test_url_takes_priority_over_the_question_mark():
    """A question *containing* a URL is a fetch, not a docs search."""
    call = decide_fake_tool_call("what is https://example.com about?", ALL_TOOLS)
    assert call is not None
    assert call.name == "fetch_url"
    assert json.loads(call.arguments) == {"url": "https://example.com"}


# --- end-to-end parity across every backend ----------------------------------


def _client_for(backend: AgentBackendName) -> TestClient:
    settings = HermeticSettings(llm_provider="fake", agent_backend=backend, mcp_enabled=False)
    return TestClient(
        create_app(
            settings,
            redis_client=FakeAsyncRedis(decode_responses=True),
            llm=FakeLLM(),
            retriever=build_seeded_retriever(),
        )
    )


@pytest.mark.parametrize("backend", BACKENDS)
def test_url_question_calls_fetch_url_on_every_backend(backend: AgentBackendName):
    """The drift that motivated this file: pydantic_ai skipped fetch_url."""
    with _client_for(backend) as client, client.websocket_connect(f"/chat?backend={backend}") as ws:
        ws.receive_json()  # session frame
        ws.send_json({"type": "user_message", "content": "what is https://example.com/x about?"})
        events = collect_until_final(ws)

    tool_calls = [event for event in events if event["type"] == "tool_call"]
    assert tool_calls, f"{backend} made no tool call for a URL question"
    assert tool_calls[0]["tool"] == "fetch_url"
    assert tool_calls[0]["arguments"]["url"] == "https://example.com/x"


@pytest.mark.parametrize("backend", BACKENDS)
def test_docs_question_calls_search_docs_on_every_backend(backend: AgentBackendName):
    with _client_for(backend) as client, client.websocket_connect(f"/chat?backend={backend}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": "Which service generates PDF invoices?"})
        events = collect_until_final(ws)

    tool_calls = [event for event in events if event["type"] == "tool_call"]
    assert tool_calls, f"{backend} made no tool call for a docs question"
    assert tool_calls[0]["tool"] == "search_docs"


# --- FakeLLM renders the shared decision faithfully ---------------------------


async def test_fake_llm_emits_the_decided_call():
    message = ChatMessage(role="user", content="search code for def build_llm")
    specs = [ToolSpec(name=name, description="", parameters={}) for name in ALL_TOOLS]
    events = [event async for event in FakeLLM().stream_step([message], tools=specs)]
    assert len(events) == 1
    call = events[0]
    assert isinstance(call, ToolCallRequest)
    assert call.name == "code__search_code"
    assert json.loads(call.arguments) == {"pattern": "def build_llm"}
