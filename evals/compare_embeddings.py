"""Embedding model comparison on the golden set.

Providers are auto-detected from configured keys:
- hash-512  — always (offline baseline, zero cost)
- openai    — when ASSISTANT_EMBEDDING_API_KEY is set (text-embedding-3-small)
- voyage    — when ASSISTANT_VOYAGE_API_KEY is set (voyage-3)

Each provider gets its own in-process Qdrant collection (embeddings from
different models are never comparable vectors), the golden set runs against
each with hybrid search + lexical rerank, and the results table is written
to evals/results-embeddings.md.

Usage:
    uv run python -m evals.compare_embeddings [--k 5]
"""

import argparse
import asyncio
from pathlib import Path

from qdrant_client import AsyncQdrantClient

from assistant.config import Settings
from assistant.rag.embeddings import build_embedder
from assistant.rag.ingest import ingest
from assistant.rag.rerank import LexicalReranker
from assistant.rag.retriever import Retriever
from assistant.rag.store import VectorStore
from evals.run_retrieval import CORPUS, evaluate, load_golden

RESULTS = Path(__file__).parent / "results-embeddings.md"

PROVIDER_MODELS = {
    "hash": "hash-512",
    "openai": "text-embedding-3-small",
    "voyage": "voyage-3",
}


def available_providers(settings: Settings) -> list[str]:
    providers = ["hash"]
    if settings.embedding_api_key is not None:
        providers.append("openai")
    if settings.voyage_api_key is not None:
        providers.append("voyage")
    return providers


async def run(k: int) -> None:
    base_settings = Settings()
    providers = available_providers(base_settings)
    skipped = [name for name in PROVIDER_MODELS if name not in providers]

    rows: list[tuple[str, dict[str, float]]] = []
    for provider in providers:
        settings = base_settings.model_copy(
            update={"embedding_provider": provider, "embedding_model": PROVIDER_MODELS[provider]}
        )
        embedder = build_embedder(settings)
        client = AsyncQdrantClient(":memory:")
        collection = f"docs_{provider}"
        try:
            count = await ingest(CORPUS, settings, client=client, collection=collection)
            print(f"[{embedder.model_id}] ingested {count} chunks")
            retriever = Retriever(
                embedder,
                VectorStore(client, collection),
                mode="hybrid",
                reranker=LexicalReranker(),
            )
            metrics = await evaluate(retriever, load_golden(), k, verbose=False)
            rows.append((embedder.model_id, metrics))
            print(
                f"[{embedder.model_id}] " + "   ".join(f"{n}: {v:.2f}" for n, v in metrics.items())
            )
        finally:
            await client.close()

    lines = [
        "# Embedding comparison — golden retrieval set",
        "",
        f"Corpus: `evals/corpus/` · questions: {len(load_golden())} · "
        "mode: hybrid (dense + sparse RRF) + lexical rerank",
        "",
        f"| model | recall@1 | recall@{k} | MRR |",
        "|---|---:|---:|---:|",
    ]
    for model_id, metrics in rows:
        lines.append(
            f"| {model_id} | {metrics['recall@1']:.2f} "
            f"| {metrics[f'recall@{k}']:.2f} | {metrics['mrr']:.2f} |"
        )
    if skipped:
        lines += [
            "",
            f"Not run (no API key configured): {', '.join(skipped)} — set "
            "`ASSISTANT_EMBEDDING_API_KEY` (openai) / `ASSISTANT_VOYAGE_API_KEY` "
            "(voyage) and re-run.",
        ]
    RESULTS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote {RESULTS}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare embedding models on the golden set")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(run(args.k))


if __name__ == "__main__":
    main()
