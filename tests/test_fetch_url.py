"""fetch_url tool + search_docs relevance gate (the 'Chinese chunks' fixes).

httpx is mocked — no network. The gate tests use the seeded in-memory
retriever from conftest.
"""

import json
from typing import ClassVar

import pytest

from assistant.agent.base import ChatMessage
from assistant.agent.tools import NO_RELEVANT_DOCS, make_fetch_url, make_search_docs, strip_html
from assistant.llm.client import FakeLLM, ToolCallRequest, ToolSpec
from assistant.rag.rerank import query_overlap
from tests.conftest import build_seeded_retriever_async

# --- relevance gate -------------------------------------------------------------


def test_query_overlap_counts_prefix_matches():
    assert query_overlap("how do we deploy", "The deployment pipeline runs on push") == 1
    assert query_overlap("PDF invoices", "renders PDF invoices nightly") == 2
    assert query_overlap("awsomequiz streamlit certificates", "billing renders invoices") == 0
    # A stopword-only query has nothing to gate on — chunk passes.
    assert query_overlap("what is it", "anything at all") == 1


async def test_search_docs_gates_unrelated_queries():
    retriever = await build_seeded_retriever_async()
    tool = make_search_docs(retriever)
    result = await tool.handler({"query": "awsomequiz streamlit certificates chinese pinyin"})
    # The reply now carries a live inventory + retry contract; the stable
    # first line still marks the zero-result case.
    assert result.startswith(NO_RELEVANT_DOCS)
    assert "Indexed right now:" in result
    assert "DIFFERENT terms" in result


async def test_search_docs_still_returns_relevant_chunks():
    retriever = await build_seeded_retriever_async()
    tool = make_search_docs(retriever)
    result = await tool.handler({"query": "Which service generates PDF invoices?"})
    assert "billing-service" in result
    assert "sample.md" in result


# --- fetch_url ------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._json


class FakeAsyncClient:
    routes: ClassVar[dict[str, FakeResponse]] = {}

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        for prefix, response in type(self).routes.items():
            if url.startswith(prefix):
                return response
        return FakeResponse(status_code=404)

    async def aclose(self):
        """make_fetch_url closes any client it created itself."""
        return None


@pytest.fixture
def fake_http(monkeypatch: pytest.MonkeyPatch):
    FakeAsyncClient.routes = {}
    monkeypatch.setattr("httpx.AsyncClient", FakeAsyncClient)
    return FakeAsyncClient


async def test_fetch_url_rejects_non_http_and_private_hosts():
    tool = make_fetch_url()
    assert "only http(s)" in await tool.handler({"url": "ftp://example.com/x"})
    for private in ("http://localhost:8000/", "http://127.0.0.1/", "http://192.168.1.10/x"):
        assert "refusing to fetch" in await tool.handler({"url": private})


async def test_fetch_url_github_repo_uses_the_api(fake_http):
    fake_http.routes = {
        "https://api.github.com/repos/thechekh/awsomequiz-streamlit/readme": FakeResponse(
            text="# AwsomeQuiz\nA Streamlit quiz app with AWS certification question banks."
        ),
        "https://api.github.com/repos/thechekh/awsomequiz-streamlit": FakeResponse(
            json_data={
                "full_name": "thechekh/awsomequiz-streamlit",
                "description": "Quiz app",
                "language": "Python",
                "stargazers_count": 3,
                "topics": ["streamlit"],
                "pushed_at": "2026-07-01T10:00:00Z",
            }
        ),
    }
    tool = make_fetch_url()
    result = await tool.handler({"url": "https://github.com/thechekh/awsomequiz-streamlit"})
    assert "thechekh/awsomequiz-streamlit" in result
    assert "Quiz app" in result
    assert "question banks" in result  # README content included


async def test_fetch_url_github_account_lists_repos(fake_http):
    fake_http.routes = {
        "https://api.github.com/users/thechekh/repos": FakeResponse(
            json_data=[
                {"name": "awsomequiz-streamlit", "language": "Python", "description": "Quiz"},
                {"name": "ai-workspace-assistant", "language": "Python", "description": None},
            ]
        ),
        "https://api.github.com/users/thechekh": FakeResponse(
            json_data={"login": "thechekh", "type": "User", "name": None, "public_repos": 2}
        ),
    }
    tool = make_fetch_url()
    result = await tool.handler({"url": "https://github.com/thechekh"})
    assert "GitHub account thechekh" in result
    assert "awsomequiz-streamlit" in result
    assert "(no description)" in result


async def test_fetch_url_strips_html_pages(fake_http):
    fake_http.routes = {
        "https://example.com/": FakeResponse(
            text="<html><script>evil()</script><body><h1>Hello</h1> &amp; welcome</body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
    }
    tool = make_fetch_url()
    result = await tool.handler({"url": "https://example.com/"})
    assert result == "Hello & welcome"


async def test_fetch_url_reports_http_errors(fake_http):
    fake_http.routes = {"https://example.com/gone": FakeResponse(status_code=500)}
    tool = make_fetch_url()
    result = await tool.handler({"url": "https://example.com/gone"})
    assert "HTTP 500" in result


def test_strip_html_removes_scripts_and_tags():
    page = "<style>a{}</style><p>one</p>\n<script>x</script> two"
    assert strip_html(page) == "one two"


# --- per-turn duplicate-call guard ------------------------------------------------


async def test_tool_run_blocks_duplicate_calls_within_a_turn():
    from assistant.agent.tools import Tool
    from assistant.telemetry import TurnStats, current_turn_stats

    executions = 0

    async def handler(arguments: dict[str, object]) -> str:
        nonlocal executions
        executions += 1
        return "real result"

    tool = Tool(name="t", description="", parameters={}, handler=handler)
    token = current_turn_stats.set(TurnStats())
    try:
        assert await tool.run({"x": 1}) == "real result"
        duplicate = await tool.run({"x": 1})
        assert "duplicate call" in duplicate
        assert await tool.run({"x": 2}) == "real result"  # different args still run
    finally:
        current_turn_stats.reset(token)
    assert executions == 2

    # Outside a turn (no stats bound) the guard is inert.
    assert await tool.run({"x": 1}) == "real result"


# --- FakeLLM routes URL questions to fetch_url ------------------------------------


async def test_fake_llm_calls_fetch_url_for_urls():
    specs = [
        ToolSpec(name="search_docs", description="", parameters={}),
        ToolSpec(name="fetch_url", description="", parameters={}),
    ]
    message = ChatMessage(
        role="user", content="what is https://github.com/thechekh/awsomequiz-streamlit about?"
    )
    events = [event async for event in FakeLLM().stream_step([message], tools=specs)]
    assert len(events) == 1
    call = events[0]
    assert isinstance(call, ToolCallRequest)
    assert call.name == "fetch_url"
    assert json.loads(call.arguments) == {"url": "https://github.com/thechekh/awsomequiz-streamlit"}


async def test_zero_result_reply_names_filename_matches_and_inventory():
    """The moment a search misses is when the model needs orientation.

    Observed live: 'meter percentage' missed because the component is named
    Progress.jsx — the reply must surface indexed filenames sharing a query
    token and the per-repo inventory, so the next action is a better search
    instead of a confident 'does not exist'.
    """
    from assistant.agent.tools.search_docs import _zero_result_help

    sources = [
        ("cassidoo/todometer/src/renderer/src/components/Progress.jsx", 3),
        ("cassidoo/todometer/README.md", 2),
        ("acme/handbook/docs/guide.md", 1),
        ("uploaded-notes.md", 1),
    ]
    reply = _zero_result_help("where is the progress bar drawn", sources)
    assert "cassidoo/todometer (2 files)" in reply
    assert "acme/handbook (1 files)" in reply
    assert "(uploaded files) (1 files)" in reply
    assert "Progress.jsx" in reply, "filename token match must be surfaced"
    assert "ingest_repo" in reply

    # No filename overlap -> no fabricated matches section.
    reply = _zero_result_help("kubernetes ingress", sources)
    assert "NAME matches" not in reply
