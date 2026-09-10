"""Embedder protocol + providers.

`hash` — offline feature-hashing bag-of-words (a classic IR technique):
deterministic, zero-cost, no network, and good enough for lexical matches.
It is the dev/test default so the whole RAG pipeline runs for free.

`openai` — text-embedding-3-small (~$0.02 per 1M tokens); `voyage` —
voyage-3 over raw HTTP. `evals/compare_embeddings.py` measures all three on
the golden set.

One embedder is built per process and shared by the retriever, the upload
endpoint and the repository ingester: the hosted ones own an HTTP connection
pool, and building one per upload leaked a pool each time.
"""

import asyncio
import hashlib
import math
from typing import Protocol

import httpx
from openai import AsyncOpenAI

from assistant.config import Settings
from assistant.rag.sparse import WORD_RE


class Embedder(Protocol):
    dimension: int
    model_id: str  # labels collections and eval reports

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def aclose(self) -> None:
        """Release whatever the embedder holds (a connection pool, for the hosted ones)."""
        ...


class HashEmbedder:
    """Signed feature hashing over lowercase word tokens, L2-normalized."""

    dimension = 512
    model_id = "hash-512"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Pure CPU work (md5 per token), so it would otherwise block the event
        # loop that is serving live chats while a corpus is being ingested.
        return await asyncio.to_thread(lambda: [self._embed_one(text) for text in texts])

    async def aclose(self) -> None:
        return None

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in WORD_RE.findall(text.lower()):
            digest = hashlib.md5(token.encode(), usedforsecurity=False).digest()
            slot = int.from_bytes(digest[:4], "little") % self.dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[slot] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector


class OpenAIEmbedder:
    def __init__(self, model: str, api_key: str) -> None:
        self.model_id = model
        # text-embedding-3-small -> 1536, text-embedding-3-large -> 3072
        self.dimension = 3072 if "large" in model else 1536
        # The SDK's default read timeout is ten minutes; an embedding call
        # that takes longer than a minute is a broken provider, not a slow one.
        self._client = AsyncOpenAI(api_key=api_key, timeout=60.0)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 128):
            batch = texts[start : start + 128]
            response = await self._client.embeddings.create(model=self.model_id, input=batch)
            vectors.extend(item.embedding for item in response.data)
        return vectors

    async def aclose(self) -> None:
        await self._client.close()


class VoyageEmbedder:
    """Voyage AI embeddings over raw HTTP (respx-testable, no extra SDK dep)."""

    _ENDPOINT = "https://api.voyageai.com/v1/embeddings"

    def __init__(self, model: str, api_key: str) -> None:
        self.model_id = model
        # voyage-3 family -> 1024 dims; the -lite variant -> 512
        self.dimension = 512 if "lite" in model else 1024
        self._http = httpx.AsyncClient(timeout=60, headers={"Authorization": f"Bearer {api_key}"})

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 128):
            batch = texts[start : start + 128]
            response = await self._http.post(
                self._ENDPOINT, json={"model": self.model_id, "input": batch}
            )
            response.raise_for_status()
            vectors.extend(item["embedding"] for item in response.json()["data"])
        return vectors

    async def aclose(self) -> None:
        await self._http.aclose()


def build_embedder(settings: Settings) -> Embedder:
    provider = settings.embedding_provider
    if provider == "hash":
        return HashEmbedder()
    if provider == "voyage":
        voyage_key = settings.voyage_api_key.get_secret_value() if settings.voyage_api_key else None
        if voyage_key is None:
            raise ValueError("ASSISTANT_VOYAGE_API_KEY is required for provider 'voyage'")
        return VoyageEmbedder(model=settings.embedding_model, api_key=voyage_key)
    api_key = settings.embedding_api_key.get_secret_value() if settings.embedding_api_key else None
    if api_key is None:
        raise ValueError("ASSISTANT_EMBEDDING_API_KEY is required for provider 'openai'")
    return OpenAIEmbedder(model=settings.embedding_model, api_key=api_key)
