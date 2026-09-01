"""The retrieval quality gate.

`evals/baseline.json` is the only thing standing between a chunking tweak and
a silently worse assistant, so it gets the same treatment as the code: the
floor must be real (reproducible from the corpus), and the comparison must
actually fail when quality drops.
"""

import json

import pytest
from evals.run_retrieval import (
    BASELINE,
    CORPUS,
    TOLERANCE,
    check_against_baseline,
    evaluate,
    load_golden,
)
from qdrant_client import AsyncQdrantClient

from assistant.rag.embeddings import build_embedder
from assistant.rag.ingest import ingest
from assistant.rag.rerank import LexicalReranker
from assistant.rag.retriever import Retriever
from assistant.rag.store import VectorStore
from tests.conftest import HermeticSettings


def test_baseline_file_is_well_formed() -> None:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert baseline["metrics"], "a baseline with no metrics gates nothing"
    assert set(baseline["metrics"]) == {"recall@1", "recall@5", "mrr"}
    assert all(0.0 <= value <= 1.0 for value in baseline["metrics"].values())
    # The config is what makes the numbers reproducible by someone else.
    assert baseline["config"]["questions"] == len(load_golden())


def test_a_drop_is_reported_and_a_rise_is_not() -> None:
    floor: dict[str, float] = json.loads(BASELINE.read_text(encoding="utf-8"))["metrics"]

    assert check_against_baseline(dict(floor)) == []
    assert check_against_baseline({name: value + 0.1 for name, value in floor.items()}) == []

    dropped = {name: value - 0.1 for name, value in floor.items()}
    regressions = check_against_baseline(dropped)
    assert len(regressions) == len(floor)
    assert all("baseline" in line for line in regressions)

    # Float noise must not fail the build; a real drop must.
    assert check_against_baseline({name: v - TOLERANCE / 2 for name, v in floor.items()}) == []
    assert check_against_baseline({name: v - TOLERANCE * 4 for name, v in floor.items()}) != []


@pytest.mark.slow
async def test_the_committed_baseline_is_actually_achieved() -> None:
    """Run the real golden set: the floor must be a measurement, not a wish.

    Without this, the gate could be quietly weakened (or set to numbers the
    pipeline never reached) and nothing would notice.
    """
    # The real thing: per-file sources, so `expect_source` matching is real
    # rather than trivially satisfied by one merged document.
    settings = HermeticSettings()
    client = AsyncQdrantClient(":memory:")
    try:
        await ingest(CORPUS, settings, client=client, collection="gate", recreate=True)
        retriever = Retriever(
            build_embedder(settings),
            VectorStore(client, "gate"),
            mode=settings.retrieval_mode,
            reranker=LexicalReranker() if settings.rerank_enabled else None,
        )
        metrics = await evaluate(retriever, load_golden(), k=5, verbose=False)
    finally:
        await client.close()

    baseline: dict[str, float] = json.loads(BASELINE.read_text(encoding="utf-8"))["metrics"]
    assert metrics["recall@5"] >= baseline["recall@5"] - TOLERANCE
    assert metrics["mrr"] >= baseline["mrr"] - TOLERANCE
    assert metrics["recall@1"] >= baseline["recall@1"] - TOLERANCE
