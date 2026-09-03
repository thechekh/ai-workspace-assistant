"""Ingestion pipeline: parse -> chunk -> embed -> upsert.

Usage:
    uv run python -m assistant.rag.ingest <folder> [--collection docs]

Re-running is idempotent for unchanged docs (deterministic chunk ids
overwrite in place). A one-off CLI: nothing schedules it.
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
    """Read and chunk every doc. Synchronous by design — callers on an event
    loop must go through `load_chunks_async`."""
    chunks: list[Chunk] = []
    for path in sorted(corpus_dir.rglob("*.md")):
        source = path.relative_to(corpus_dir).as_posix()
        chunks.extend(chunk_markdown(path.read_text(encoding="utf-8"), source=source))
    return chunks


async def load_chunks_async(corpus_dir: Path) -> list[Chunk]:
    """Off-loop wrapper: file reads + chunking must not stall live chats."""
    return await asyncio.to_thread(load_chunks, corpus_dir)


async def ingest_chunks(
    chunks: list[Chunk],
    settings: Settings,
    *,
    store: VectorStore,
    recreate: bool = False,
) -> int:
    """Embed and upsert already-chunked documents. The shared tail of both the
    CLI corpus path and the runtime upload endpoint.

    Ingesting a source **replaces** it: every chunk it already had is deleted
    first. Relying on the deterministic ids to overwrite is not enough — an id
    is uuid5 of (source, heading, index), so shortening a document or renaming
    a heading leaves the old chunks under ids nothing new collides with. They
    survive, stay searchable, and get cited: deleting a paragraph would not
    delete it from the answers.

    Sources are replaced individually rather than by wiping the collection,
    because one Qdrant collection holds both the corpus folder and whatever was
    uploaded at runtime — a nightly re-index must not erase someone's upload.
    A file *removed* from the corpus folder is the remaining gap; `--recreate`
    is the deliberate full rebuild for that.
    """
    if not chunks:
        return 0
    embedder = build_embedder(settings)
    dense_vectors = await embedder.embed([chunk.text for chunk in chunks])
    sparse_vectors = [encode_sparse(chunk.text) for chunk in chunks]
    await store.ensure_collection(embedder.dimension, recreate=recreate)
    if not recreate:  # recreate already dropped everything
        for source in sorted({chunk.source for chunk in chunks}):
            await store.delete_source(source)
    await store.upsert(chunks, dense_vectors, sparse_vectors)
    return len(chunks)


async def ingest_documents(
    documents: list[tuple[str, str]],
    settings: Settings,
    *,
    store: VectorStore,
) -> int:
    """Ingest documents supplied at runtime as (source_name, markdown_text).

    Chunking is CPU-bound, so it runs off the event loop — an upload must not
    stall live chats. Re-uploading a source replaces it (see `ingest_chunks`).
    """
    chunks = await asyncio.to_thread(
        lambda: [
            chunk for source, text in documents for chunk in chunk_markdown(text, source=source)
        ]
    )
    return await ingest_chunks(chunks, settings, store=store)


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
    try:
        chunks = await load_chunks_async(corpus_dir)
        return await ingest_chunks(chunks, settings, store=store, recreate=recreate)
    finally:
        if owns_client:
            await qdrant.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a Markdown corpus into Qdrant")
    parser.add_argument("corpus", type=Path, help="folder of *.md to ingest")
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
