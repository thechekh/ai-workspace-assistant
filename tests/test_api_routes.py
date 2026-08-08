"""HTTP API tests: /api/info, /api/reindex (inline mode), and bearer auth."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.websockets import WebSocketDisconnect

import assistant.api.routes as routes
from assistant.llm.client import FakeLLM
from assistant.main import create_app
from tests.conftest import HermeticSettings, build_seeded_retriever


def make_client(**settings_overrides) -> TestClient:
    settings = HermeticSettings(
        llm_provider="fake",
        mcp_enabled=False,
        redis_url="fakeredis://",
        **settings_overrides,
    )
    app = create_app(settings, llm=FakeLLM(), retriever=build_seeded_retriever())
    return TestClient(app)


def test_info_reports_platform_shape(client):
    response = client.get("/api/info")
    assert response.status_code == 200
    payload = response.json()
    assert payload["backends"] == ["custom", "langgraph", "pydantic_ai"]
    assert payload["llm_provider"] == "fake"
    assert payload["retrieval_mode"] == "hybrid"
    assert payload["auth_required"] is False


def test_reindex_inline_mode(monkeypatch: pytest.MonkeyPatch):
    async def fake_ingest(corpus, settings, **kwargs) -> int:
        return 30

    monkeypatch.setattr(routes, "ingest", fake_ingest)
    with make_client(corpus_dir=Path("evals/corpus")) as client:
        response = client.post("/api/reindex")
    assert response.status_code == 200
    assert response.json() == {"mode": "inline", "chunks": 30}


def test_reindex_failure_returns_503(monkeypatch: pytest.MonkeyPatch):
    async def broken_ingest(corpus, settings, **kwargs) -> int:
        raise RuntimeError("qdrant unreachable")

    monkeypatch.setattr(routes, "ingest", broken_ingest)
    with make_client(corpus_dir=Path("evals/corpus")) as client:
        response = client.post("/api/reindex")
    assert response.status_code == 503
    assert "qdrant unreachable" in response.json()["detail"]


def test_bearer_auth_guards_reindex_but_not_info(monkeypatch: pytest.MonkeyPatch):
    async def fake_ingest(corpus, settings, **kwargs) -> int:
        return 1

    monkeypatch.setattr(routes, "ingest", fake_ingest)
    with make_client(auth_token=SecretStr("s3cret"), corpus_dir=Path("evals/corpus")) as client:
        assert client.get("/api/info").status_code == 200
        assert client.get("/api/info").json()["auth_required"] is True

        assert client.post("/api/reindex").status_code == 401
        assert (
            client.post("/api/reindex", headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )
        ok = client.post("/api/reindex", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200


def test_ws_requires_token_when_auth_enabled():
    with make_client(auth_token=SecretStr("s3cret")) as client:
        # wrong/missing token: server closes the socket with policy violation
        with client.websocket_connect("/chat") as ws, pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 1008

        # correct token: normal session bootstrap
        with client.websocket_connect("/chat?token=s3cret") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "session"
