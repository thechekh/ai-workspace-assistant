"""Query-time retrieval.

Pipeline: embed the query (dense; plus sparse lexical when mode="hybrid"),
fetch candidates from Qdrant (RRF fusion in hybrid mode), then optionally
rerank the top candidates before returning the final top-k.
"""

import time

import structlog

from assistant.config import RetrievalMode
from assistant.rag.embeddings import Embedder
from assistant.rag.rerank import Reranker
from assistant.rag.sparse import encode_sparse
from assistant.rag.store import RetrievedChunk, VectorStore
from assistant.telemetry import RETRIEVAL_SECONDS, tracer

logger = structlog.get_logger("assistant.rag")


class Retriever:
    def __init__(
        self,
        embedder: Embedder,
        store: VectorStore,
        *,
        mode: RetrievalMode = "hybrid",
        reranker: Reranker | None = None,
        fetch_limit: int = 20,
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._mode = mode
        self._reranker = reranker
        self._fetch_limit = fetch_limit

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        source: str | None = None,
    ) -> list[RetrievedChunk]:
        start = time.perf_counter()
        with tracer.start_as_current_span("rag.retrieve") as span:
            span.set_attribute("rag.mode", self._mode)
            span.set_attribute("rag.rerank", self._reranker is not None)
            span.set_attribute("rag.query_chars", len(query))
            [dense_vector] = await self._embedder.embed([query])
            sparse_vector = encode_sparse(query) if self._mode == "hybrid" else None
            fetch = max(self._fetch_limit, limit) if self._reranker else limit
            candidates = await self._store.search(
                dense_vector, sparse_vector=sparse_vector, limit=fetch, source=source
            )
            span.set_attribute("rag.candidates", len(candidates))
            if self._reranker:
                candidates = self._reranker.rerank(query, candidates, limit=limit)
            results = candidates[:limit]
            span.set_attribute("rag.results", len(results))
            if results:
                span.set_attribute("rag.top_score", results[0].score)

        duration = time.perf_counter() - start
        RETRIEVAL_SECONDS.labels(mode=self._mode).observe(duration)
        logger.info(
            "rag.retrieved",
            mode=self._mode,
            results=len(results),
            top_source=results[0].source if results else None,
            top_score=round(results[0].score, 3) if results else None,
            duration_ms=round(duration * 1000),
        )
        return results
