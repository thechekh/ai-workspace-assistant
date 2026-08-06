"""Golden-set retrieval evaluation: recall@1, recall@k, MRR.

Usage:
    uv run python evals/run_retrieval.py [--k 5] [--collection docs]
        [--mode hybrid|dense] [--no-rerank] [--memory]

--memory runs fully self-contained: in-process Qdrant (:memory:), corpus
ingested on the fly — no containers needed (also used by CI and the
embedding comparison). Without it, an ingested collection is required first:

    uv run python -m assistant.rag.ingest docs_corpus --recreate
"""

import argparse
import asyncio
from pathlib import Path
from typing import cast

import yaml
from qdrant_client import AsyncQdrantClient

from assistant.config import RetrievalMode, Settings
from assistant.rag.embeddings import build_embedder
from assistant.rag.ingest import ingest
from assistant.rag.rerank import LexicalReranker
from assistant.rag.retriever import Retriever
from assistant.rag.store import VectorStore

GOLDEN = Path(__file__).parent / "golden.yaml"
CORPUS = Path(__file__).parent.parent / "docs_corpus"


def load_golden() -> list[dict[str, str]]:
    return cast("list[dict[str, str]]", yaml.safe_load(GOLDEN.read_text(encoding="utf-8")))


async def evaluate(
    retriever: Retriever, items: list[dict[str, str]], k: int, *, verbose: bool = True
) -> dict[str, float]:
    ranks: list[int | None] = []
    for item in items:
        results = await retriever.search(item["question"], limit=k)
        rank: int | None = None
        expected_text = item.get("expect_text")
        for position, result in enumerate(results, start=1):
            if result.source == item["expect_source"] and (
                expected_text is None or expected_text.lower() in result.text.lower()
            ):
                rank = position
                break
        ranks.append(rank)
        if verbose:
            marker = f"rank {rank}" if rank else "MISS  "
            print(f"  [{marker}] {item['question']}")

    total = len(ranks)
    return {
        "recall@1": sum(1 for r in ranks if r == 1) / total,
        f"recall@{k}": sum(1 for r in ranks if r is not None) / total,
        "mrr": sum(1 / r for r in ranks if r is not None) / total,
    }


async def run(k: int, collection: str | None, mode: RetrievalMode, rerank: bool, memory: bool):
    settings = Settings()
    embedder = build_embedder(settings)

    if memory:
        client = AsyncQdrantClient(":memory:")
        target = collection or settings.qdrant_collection
        count = await ingest(CORPUS, settings, client=client, collection=target, recreate=True)
        print(f"(memory mode: ingested {count} chunks)")
    else:
        client = AsyncQdrantClient(url=settings.qdrant_url)
        target = collection or settings.qdrant_collection

    retriever = Retriever(
        embedder,
        VectorStore(client, target),
        mode=mode,
        reranker=LexicalReranker() if rerank else None,
    )
    try:
        metrics = await evaluate(retriever, load_golden(), k)
    finally:
        await client.close()

    print()
    print(f"embedder: {embedder.model_id}   mode: {mode}   rerank: {rerank}")
    print("   ".join(f"{name}: {value:.2f}" for name, value in metrics.items()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Golden-set retrieval evaluation")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--collection", default=None, help="override ASSISTANT_QDRANT_COLLECTION")
    parser.add_argument("--mode", choices=["hybrid", "dense"], default="hybrid")
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument(
        "--memory", action="store_true", help="in-process Qdrant + on-the-fly ingest (no Docker)"
    )
    args = parser.parse_args()
    asyncio.run(
        run(
            args.k,
            args.collection,
            cast("RetrievalMode", args.mode),
            not args.no_rerank,
            args.memory,
        )
    )


if __name__ == "__main__":
    main()
