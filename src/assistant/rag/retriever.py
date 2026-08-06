"""Query-time retrieval.

Pipeline: embed the query (dense; plus sparse lexical when mode="hybrid"),
fetch candidates from Qdrant (RRF fusion in hybrid mode), then optionally
rerank the top candidates before returning the final top-k.
"""

from assistant.config import RetrievalMode
from assistant.rag.embeddings import Embedder
from assistant.rag.rerank import Reranker
from assistant.rag.sparse import encode_sparse
from assistant.rag.store import RetrievedChunk, VectorStore


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
        [dense_vector] = await self._embedder.embed([query])
        sparse_vector = encode_sparse(query) if self._mode == "hybrid" else None
        fetch = max(self._fetch_limit, limit) if self._reranker else limit
        candidates = await self._store.search(
            dense_vector, sparse_vector=sparse_vector, limit=fetch, source=source
        )
        if self._reranker:
            candidates = self._reranker.rerank(query, candidates, limit=limit)
        return candidates[:limit]
