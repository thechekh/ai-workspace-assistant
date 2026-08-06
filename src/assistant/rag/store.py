"""Qdrant wrapper: one collection with named dense + sparse vectors.

Every chunk carries a dense vector (semantic/hash) and a sparse lexical
vector; search runs dense-only or hybrid (dense + sparse fused with
reciprocal rank fusion via the Query API). The collection is recreated
automatically if an older single-vector schema is found — chunks are
derived data, re-ingesting is cheap and idempotent.
"""

import logging

from pydantic import BaseModel
from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm

from assistant.rag.chunking import Chunk

logger = logging.getLogger(__name__)

DENSE = "dense"
SPARSE = "lexical"

SparseVector = tuple[list[int], list[float]]


class RetrievedChunk(BaseModel):
    text: str
    source: str
    heading: str
    score: float


class VectorStore:
    def __init__(self, client: AsyncQdrantClient, collection: str) -> None:
        self._client = client
        self.collection = collection

    async def ensure_collection(self, dimension: int, *, recreate: bool = False) -> None:
        if await self._client.collection_exists(self.collection):
            if not recreate and await self._has_expected_schema(dimension):
                return
            logger.info("recreating collection %r (schema change requested)", self.collection)
            await self._client.delete_collection(self.collection)
        await self._client.create_collection(
            collection_name=self.collection,
            vectors_config={DENSE: qm.VectorParams(size=dimension, distance=qm.Distance.COSINE)},
            sparse_vectors_config={SPARSE: qm.SparseVectorParams()},
        )

    async def _has_expected_schema(self, dimension: int) -> bool:
        info = await self._client.get_collection(self.collection)
        vectors = info.config.params.vectors
        if not isinstance(vectors, dict) or DENSE not in vectors:
            return False
        return vectors[DENSE].size == dimension

    async def upsert(
        self,
        chunks: list[Chunk],
        dense_vectors: list[list[float]],
        sparse_vectors: list[SparseVector],
    ) -> None:
        points = [
            qm.PointStruct(
                id=chunk.id,
                vector={
                    DENSE: dense,
                    SPARSE: qm.SparseVector(indices=indices, values=values),
                },
                payload={
                    "text": chunk.text,
                    "source": chunk.source,
                    "heading": chunk.heading,
                    "index": chunk.index,
                },
            )
            for chunk, dense, (indices, values) in zip(
                chunks, dense_vectors, sparse_vectors, strict=True
            )
        ]
        await self._client.upsert(collection_name=self.collection, points=points)

    async def search(
        self,
        dense_vector: list[float],
        *,
        sparse_vector: SparseVector | None = None,
        limit: int = 5,
        source: str | None = None,
    ) -> list[RetrievedChunk]:
        query_filter = None
        if source:
            query_filter = qm.Filter(
                must=[qm.FieldCondition(key="source", match=qm.MatchValue(value=source))]
            )

        if sparse_vector is not None:
            indices, values = sparse_vector
            prefetch_limit = max(limit * 4, 20)
            response = await self._client.query_points(
                collection_name=self.collection,
                prefetch=[
                    qm.Prefetch(
                        query=dense_vector, using=DENSE, limit=prefetch_limit, filter=query_filter
                    ),
                    qm.Prefetch(
                        query=qm.SparseVector(indices=indices, values=values),
                        using=SPARSE,
                        limit=prefetch_limit,
                        filter=query_filter,
                    ),
                ],
                query=qm.FusionQuery(fusion=qm.Fusion.RRF),
                limit=limit,
                query_filter=query_filter,
                with_payload=True,
            )
        else:
            response = await self._client.query_points(
                collection_name=self.collection,
                query=dense_vector,
                using=DENSE,
                limit=limit,
                query_filter=query_filter,
                with_payload=True,
            )

        results: list[RetrievedChunk] = []
        for point in response.points:
            payload = point.payload or {}
            results.append(
                RetrievedChunk(
                    text=str(payload.get("text", "")),
                    source=str(payload.get("source", "")),
                    heading=str(payload.get("heading", "")),
                    score=point.score,
                )
            )
        return results
