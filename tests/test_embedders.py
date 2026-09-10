"""The hosted embedders, offline: the HTTP they send and how they batch.

Two different fakes, because the two providers no longer share a transport.
`VoyageEmbedder` is our own httpx code, so `respx` answers for it. The OpenAI
SDK moved to **httpx2** in 3.0, which respx does not patch — an httpx2
`MockTransport` stands in there instead. Getting that wrong is not a failing
test but a silent live request, which is what `no_outbound_network` in
conftest now catches.

These are the branches the offline `hash` default never exercises.
"""

import json

import httpx
import httpx2
import pytest
import respx
from openai import AsyncOpenAI
from pydantic import SecretStr

from assistant.rag.embeddings import (
    HashEmbedder,
    OpenAIEmbedder,
    VoyageEmbedder,
    build_embedder,
)
from tests.conftest import HermeticSettings


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

    def fake_client(**kwargs: object) -> AsyncOpenAI:
        kwargs.pop("timeout", None)  # the transport answers instantly
        return AsyncOpenAI(
            **kwargs,  # type: ignore[arg-type]
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


@respx.mock
async def test_voyage_embedder_sends_the_token_and_batches():
    def reply(request: httpx.Request) -> httpx.Response:
        import json

        inputs = json.loads(request.content)["input"]
        return httpx.Response(
            200, json={"data": [{"embedding": [0.5, 0.5]} for _ in inputs], "model": "voyage-3"}
        )

    route = respx.post("https://api.voyageai.com/v1/embeddings").mock(side_effect=reply)
    embedder = VoyageEmbedder(model="voyage-3", api_key="pa-test")
    try:
        vectors = await embedder.embed(["a"] * 129)
    finally:
        await embedder.aclose()

    assert len(vectors) == 129
    assert route.call_count == 2
    assert route.calls[0].request.headers["authorization"] == "Bearer pa-test"
    assert embedder.dimension == 1024
    assert VoyageEmbedder(model="voyage-3-lite", api_key="x").dimension == 512


@respx.mock
async def test_voyage_embedder_raises_on_provider_errors():
    respx.post("https://api.voyageai.com/v1/embeddings").mock(
        return_value=httpx.Response(401, json={"detail": "bad key"})
    )
    embedder = VoyageEmbedder(model="voyage-3", api_key="wrong")
    try:
        with pytest.raises(httpx.HTTPStatusError):
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
