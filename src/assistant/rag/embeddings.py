"""Embedder protocol + providers.

`hash` — offline feature-hashing bag-of-words (a classic IR technique):
deterministic, zero-cost, no network, and good enough for lexical matches.
It is the dev/test default so the whole RAG pipeline runs for free.

`openai` — text-embedding-3-small (~$0.02 per 1M tokens). Phase 7 adds
voyage-3 for the measured comparison.
"""

import asyncio
import hashlib
import math
import re
from typing import Protocol

import httpx
from openai import AsyncOpenAI

from assistant.config import Settings

_TOKEN_RE = re.compile(r"\w+")


class Embedder(Protocol):
    dimension: int
    model_id: str  # labels collections and eval reports

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    """Signed feature hashing over lowercase word tokens, L2-normalized."""

    dimension = 512
    model_id = "hash-512"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Pure CPU work (md5 per token), so it would otherwise block the event
        # loop that is serving live chats while a corpus is being ingested.
        return await asyncio.to_thread(lambda: [self._embed_one(text) for text in texts])

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in _TOKEN_RE.findall(text.lower()):
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
        self._client = AsyncOpenAI(api_key=api_key)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 128):
            batch = texts[start : start + 128]
            response = await self._client.embeddings.create(model=self.model_id, input=batch)
            vectors.extend(item.embedding for item in response.data)
        return vectors


class VoyageEmbedder:
    """Voyage AI embeddings over raw HTTP (respx-testable, no extra SDK dep)."""

    _ENDPOINT = "https://api.voyageai.com/v1/embeddings"

    def __init__(self, model: str, api_key: str) -> None:
        self.model_id = model
        # voyage-3 family -> 1024 dims; the -lite variant -> 512
        self.dimension = 512 if "lite" in model else 1024
        self._api_key = api_key

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        async with httpx.AsyncClient(timeout=60) as http:
            for start in range(0, len(texts), 128):
                batch = texts[start : start + 128]
                response = await http.post(
                    self._ENDPOINT,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={"model": self.model_id, "input": batch},
                )
                response.raise_for_status()
                vectors.extend(item["embedding"] for item in response.json()["data"])
        return vectors


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
