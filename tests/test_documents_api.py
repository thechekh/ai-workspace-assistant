"""Documents added in flight — the knowledge base starts empty.

No seed corpus ships with the app: documents are uploaded at runtime through
POST /api/documents. These tests use the in-memory Qdrant from conftest, so
they exercise the real chunk -> embed -> upsert path.
"""

from collections.abc import AsyncIterator

from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient
from pydantic import SecretStr

from assistant.agent.base import AgentEvent, ChatMessage, FinalEvent
from assistant.agent.tools import NOTHING_INDEXED, make_search_docs
from assistant.llm.client import FakeLLM
from assistant.main import create_app
from assistant.rag.embeddings import HashEmbedder
from assistant.rag.retriever import Retriever
from assistant.rag.store import VectorStore
from tests.conftest import HermeticSettings, build_seeded_retriever

DOC = b"""# Payments

## payment-adapter

Talks to Stripe and reconciles settlement reports every morning.
"""


def _client(**overrides) -> TestClient:
    """An app whose knowledge base is the seeded in-memory retriever."""
    settings = HermeticSettings(llm_provider="fake", mcp_enabled=False, **overrides)
    return TestClient(
        create_app(
            settings,
            redis_client=FakeAsyncRedis(decode_responses=True),
            llm=FakeLLM(),
            retriever=build_seeded_retriever(),
        )
    )


def test_list_documents_reports_indexed_sources():
    with _client() as client:
        payload = client.get("/api/documents").json()
    # conftest seeds one document.
    assert [doc["source"] for doc in payload["documents"]] == ["sample.md"]
    assert payload["total_chunks"] == sum(doc["chunks"] for doc in payload["documents"])
    assert payload["total_chunks"] > 0


def test_upload_file_becomes_searchable():
    with _client() as client:
        response = client.post(
            "/api/documents", files={"files": ("payments.md", DOC, "text/markdown")}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["chunks"] > 0
        assert [doc["source"] for doc in body["indexed"]] == ["payments.md"]

        sources = [doc["source"] for doc in client.get("/api/documents").json()["documents"]]
        assert "payments.md" in sources

        # And the agent can now answer from it.
        with client.websocket_connect("/chat") as ws:
            ws.receive_json()
            ws.send_json({"type": "user_message", "content": "Which service talks to Stripe?"})
            events = []
            while True:
                event = ws.receive_json()
                events.append(event)
                if event["type"] in {"final", "error"}:
                    break
        results = [e["result"] for e in events if e["type"] == "tool_result"]
        assert any("payment-adapter" in result for result in results)


def test_upload_pasted_text_with_a_name():
    with _client() as client:
        response = client.post(
            "/api/documents", data={"text": "# Runbook\n\nRestart the pod.", "source": "runbook.md"}
        )
        assert response.status_code == 200
        assert [doc["source"] for doc in response.json()["indexed"]] == ["runbook.md"]


def test_reuploading_a_source_replaces_it_rather_than_duplicating():
    with _client() as client:
        first = client.post(
            "/api/documents", files={"files": ("payments.md", DOC, "text/markdown")}
        )
        chunks = first.json()["chunks"]
        client.post("/api/documents", files={"files": ("payments.md", DOC, "text/markdown")})

        documents = {
            doc["source"]: doc["chunks"] for doc in client.get("/api/documents").json()["documents"]
        }
    assert documents["payments.md"] == chunks  # not doubled


def test_unsupported_and_empty_uploads_are_rejected_clearly():
    with _client() as client:
        skipped = client.post(
            "/api/documents", files={"files": ("photo.png", b"\x89PNG binary", "image/png")}
        )
        assert skipped.status_code == 400
        assert "unsupported type" in skipped.json()["detail"]

        assert client.post("/api/documents", data={}).status_code == 400


def test_delete_document_removes_its_chunks():
    with _client() as client:
        client.post("/api/documents", files={"files": ("payments.md", DOC, "text/markdown")})
        removed = client.delete("/api/documents/payments.md")
        assert removed.status_code == 200
        assert removed.json()["removed_chunks"] > 0

        sources = [doc["source"] for doc in client.get("/api/documents").json()["documents"]]
        assert "payments.md" not in sources
        assert "sample.md" in sources  # other documents untouched


def test_delete_unknown_document_404s():
    with _client() as client:
        assert client.delete("/api/documents/nope.md").status_code == 404


def test_writes_require_the_token_but_listing_does_not():
    with _client(auth_token=SecretStr("s3cret")) as client:
        assert client.get("/api/documents").status_code == 200
        assert (
            client.post("/api/documents", data={"text": "x", "source": "a.md"}).status_code == 401
        )
        assert client.delete("/api/documents/sample.md").status_code == 401

        ok = client.post(
            "/api/documents",
            data={"text": "# Note\n\nbody", "source": "a.md"},
            headers={"Authorization": "Bearer s3cret"},
        )
        assert ok.status_code == 200


async def test_search_docs_says_the_index_is_empty_rather_than_no_match():
    """An empty knowledge base is the user's problem to fix, not a bad query."""
    from qdrant_client import AsyncQdrantClient

    empty = Retriever(HashEmbedder(), VectorStore(AsyncQdrantClient(":memory:"), "docs"))
    tool = make_search_docs(empty)
    assert await tool.handler({"query": "anything at all"}) == NOTHING_INDEXED


def test_documents_api_needs_a_store():
    """With no retriever and no Qdrant, the endpoint says so instead of crashing."""
    settings = HermeticSettings(llm_provider="fake", mcp_enabled=False)

    class DummyAgent:
        async def run(
            self, history: list[ChatMessage], user_message: str
        ) -> AsyncIterator[AgentEvent]:
            yield FinalEvent(content="")  # pragma: no cover — never run

    app = create_app(
        settings,
        redis_client=FakeAsyncRedis(decode_responses=True),
        llm=FakeLLM(),
        agent=DummyAgent(),
    )
    with TestClient(app) as client:
        assert client.get("/api/documents").status_code == 503
