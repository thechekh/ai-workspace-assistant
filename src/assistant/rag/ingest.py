"""Ingestion pipeline: parse -> chunk -> embed -> upsert.

Usage:
    uv run python -m assistant.rag.ingest [docs_corpus] [--collection docs]

Re-running is idempotent for unchanged docs (deterministic chunk ids
overwrite in place). Runs as a taskiq background job from Phase 8.
"""

import argparse
import asyncio
from pathlib import Path

from qdrant_client import AsyncQdrantClient

from assistant.config import Settings
from assistant.rag.chunking import Chunk, chunk_markdown
from assistant.rag.embeddings import build_embedder
from assistant.rag.sparse import encode_sparse
from assistant.rag.store import VectorStore


def load_chunks(corpus_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(corpus_dir.rglob("*.md")):
        source = path.relative_to(corpus_dir).as_posix()
        chunks.extend(chunk_markdown(path.read_text(encoding="utf-8"), source=source))
    return chunks


async def ingest(
    corpus_dir: Path,
    settings: Settings,
    *,
    client: AsyncQdrantClient | None = None,
    collection: str | None = None,
    recreate: bool = False,
) -> int:
    """Ingest every *.md under corpus_dir; returns the number of chunks."""
    owns_client = client is None
    qdrant = client or AsyncQdrantClient(url=settings.qdrant_url)
    store = VectorStore(qdrant, collection or settings.qdrant_collection)
    embedder = build_embedder(settings)
    try:
        chunks = load_chunks(corpus_dir)
        if not chunks:
            return 0
        dense_vectors = await embedder.embed([chunk.text for chunk in chunks])
        sparse_vectors = [encode_sparse(chunk.text) for chunk in chunks]
        await store.ensure_collection(embedder.dimension, recreate=recreate)
        await store.upsert(chunks, dense_vectors, sparse_vectors)
        return len(chunks)
    finally:
        if owns_client:
            await qdrant.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a Markdown corpus into Qdrant")
    parser.add_argument("corpus", type=Path, nargs="?", default=Path("docs_corpus"))
    parser.add_argument("--collection", default=None, help="override ASSISTANT_QDRANT_COLLECTION")
    parser.add_argument(
        "--recreate", action="store_true", help="drop and recreate the collection first"
    )
    args = parser.parse_args()

    settings = Settings()
    count = asyncio.run(
        ingest(args.corpus, settings, collection=args.collection, recreate=args.recreate)
    )
    collection = args.collection or settings.qdrant_collection
    print(
        f"ingested {count} chunks from {args.corpus} "
        f"(embedder={settings.embedding_provider}, collection={collection})"
    )


if __name__ == "__main__":
    main()
