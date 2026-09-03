"""HTTP API tests: /api/info and bearer auth on the write endpoints."""

import pytest
from pydantic import SecretStr
from starlette.websockets import WebSocketDisconnect

from tests.conftest import make_client


def test_info_reports_platform_shape(client):
    response = client.get("/api/info")
    assert response.status_code == 200
    payload = response.json()
    assert payload["backends"] == ["custom", "langgraph", "pydantic_ai"]
    assert payload["llm_provider"] == "fake"
    assert payload["retrieval_mode"] == "hybrid"
    assert payload["auth_required"] is False


def test_bearer_auth_guards_writes_but_not_info() -> None:
    """`/api/info` stays open so a browser can bootstrap; writes do not.

    Retargeted from `/api/reindex` when the background-job layer was removed:
    the endpoint went away, the guard it was covering did not.
    """
    with make_client(auth_token=SecretStr("s3cret")) as client:
        assert client.get("/api/info").status_code == 200
        assert client.get("/api/info").json()["auth_required"] is True

        upload = {"source": "guarded.md", "text": "# guarded\n\nsome content"}
        assert client.post("/api/documents", data=upload).status_code == 401
        assert (
            client.post(
                "/api/documents", data=upload, headers={"Authorization": "Bearer wrong"}
            ).status_code
            == 401
        )
        ok = client.post("/api/documents", data=upload, headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200, ok.text


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
