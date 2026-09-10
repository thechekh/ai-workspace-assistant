"""The hosted embedders, offline: the HTTP they send and how they batch.

Both providers ride httpx2 — Voyage because our own code moved there with the
SDKs, OpenAI because its SDK did in 3.0 — so both are answered by an httpx2
`MockTransport`: `MockHTTP` from conftest for Voyage, whose client we want to
build itself (auth header included), and a patched client factory for OpenAI,
whose client the SDK owns. Getting this wrong is not a failing test but a
silent live request, which is what `no_outbound_network` in conftest catches.

These are the branches the offline `hash` default never exercises.
"""

import json
from typing import Any

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr

from assistant.rag.embeddings import (
    HashEmbedder,
    OpenAIEmbedder,
    VoyageEmbedder,
    build_embedder,
)
from tests.conftest import HermeticSettings, MockHTTP


def _openai_reply(request: httpx2.Request) -> httpx2.Response:
    inputs = json.loads(request.content)["input"]
    return httpx2.Response(
        200,
        json={
            "object": "list",
            "model": "text-embedding-3-small",
            "data": [
                {"object": "embedding", "index": i, "embedding": [float(i), 1.0]}
                for i in range(len(inputs))
            ],
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        },
    )


def _mock_openai(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx2.Request]:
    """Build every AsyncOpenAI in the embedder module on a mock transport."""
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    # `Any`: these are forwarded straight into AsyncOpenAI's typed __init__.
    def fake_client(**kwargs: Any) -> AsyncOpenAI:
        kwargs.pop("timeout", None)  # the transport answers instantly
        return AsyncOpenAI(
            **kwargs,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(record)),
        )

    monkeypatch.setattr("assistant.rag.embeddings.AsyncOpenAI", fake_client)
    return seen


async def test_openai_embedder_batches_128_texts_per_request(monkeypatch: pytest.MonkeyPatch):
    requests = _mock_openai(monkeypatch, _openai_reply)
    embedder = OpenAIEmbedder(model="text-embedding-3-small", api_key="sk-test")
    try:
        vectors = await embedder.embed([f"text {i}" for i in range(130)])
    finally:
        await embedder.aclose()

    assert len(vectors) == 130
    assert len(requests) == 2  # 128 + 2
    assert embedder.dimension == 1536
    assert requests[0].headers["authorization"] == "Bearer sk-test"
    assert [len(json.loads(request.content)["input"]) for request in requests] == [128, 2]


async def test_voyage_embedder_sends_the_token_and_batches():
    def reply(request: httpx2.Request) -> httpx2.Response:
        inputs = json.loads(request.content)["input"]
        return httpx2.Response(
            200, json={"data": [{"embedding": [0.5, 0.5]} for _ in inputs], "model": "voyage-3"}
        )

    http = MockHTTP()
    route = http.post("https://api.voyageai.com/v1/embeddings", respond=reply)
    embedder = VoyageEmbedder(model="voyage-3", api_key="pa-test", transport=http.transport())
    try:
        vectors = await embedder.embed(["a"] * 129)
    finally:
        await embedder.aclose()

    assert len(vectors) == 129
    assert len(route.requests) == 2
    assert route.requests[0].headers["authorization"] == "Bearer pa-test"
    assert [len(json.loads(r.content)["input"]) for r in route.requests] == [128, 1]
    assert embedder.dimension == 1024
    assert VoyageEmbedder(model="voyage-3-lite", api_key="x").dimension == 512


async def test_voyage_embedder_raises_on_provider_errors():
    http = MockHTTP()
    http.post("https://api.voyageai.com/v1/embeddings", status=401, json={"detail": "bad key"})
    embedder = VoyageEmbedder(model="voyage-3", api_key="wrong", transport=http.transport())
    try:
        with pytest.raises(httpx2.HTTPStatusError):
            await embedder.embed(["a"])
    finally:
        await embedder.aclose()


def test_build_embedder_requires_the_matching_key():
    with pytest.raises(ValueError, match="ASSISTANT_EMBEDDING_API_KEY"):
        build_embedder(HermeticSettings(embedding_provider="openai"))
    with pytest.raises(ValueError, match="ASSISTANT_VOYAGE_API_KEY"):
        build_embedder(HermeticSettings(embedding_provider="voyage"))

    openai = build_embedder(
        HermeticSettings(embedding_provider="openai", embedding_api_key=SecretStr("sk-x"))
    )
    assert isinstance(openai, OpenAIEmbedder)
    voyage = build_embedder(
        HermeticSettings(
            embedding_provider="voyage", embedding_model="voyage-3", voyage_api_key=SecretStr("pa")
        )
    )
    assert isinstance(voyage, VoyageEmbedder)
    assert isinstance(build_embedder(HermeticSettings()), HashEmbedder)


async def test_hash_embedder_is_deterministic_and_closes_quietly():
    embedder = HashEmbedder()
    [first], [second] = await embedder.embed(["invoices"]), await embedder.embed(["invoices"])
    assert first == second
    assert len(first) == 512
    await embedder.aclose()  # nothing to release; must still be callable
