"""Golden-set retrieval evaluation: recall@1, recall@k, MRR.

Usage:
    uv run python evals/run_retrieval.py [--k 5] [--collection docs]
        [--mode hybrid|dense] [--no-rerank] [--memory]
        [--check] [--record] [--trend] [--json]

--memory runs fully self-contained: in-process Qdrant (:memory:), corpus
ingested on the fly — no containers needed (also used by CI and the
embedding comparison). Without it, an ingested collection is required first:

    uv run python -m assistant.rag.ingest evals/corpus --recreate

Retrieval quality is the one thing here no unit test can protect: a chunking
tweak or a change to the fusion weights can keep every test green while
quietly making answers worse. So the numbers are a build artifact — `--check`
fails the build when they fall below `baseline.json`, and `--record` appends
each run to `history.jsonl` so the trend stays visible.
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import structlog
import yaml
from qdrant_client import AsyncQdrantClient

from assistant.config import RetrievalMode, Settings
from assistant.rag.embeddings import build_embedder
from assistant.rag.ingest import ingest
from assistant.rag.rerank import LexicalReranker
from assistant.rag.retriever import Retriever
from assistant.rag.store import VectorStore

GOLDEN = Path(__file__).parent / "golden.yaml"
CORPUS = Path(__file__).parent / "corpus"
BASELINE = Path(__file__).parent / "baseline.json"
HISTORY = Path(__file__).parent / "history.jsonl"

# The default configuration (hash embedder + lexical rerank) is deterministic,
# so the gate is near-exact: this slack absorbs float rounding, not a real drop.
TOLERANCE = 0.005


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


def git_sha() -> str:
    """Whatever identifies this build: CI's env var, else the local checkout."""
    if sha := os.environ.get("GITHUB_SHA"):
        return sha[:12]
    git = shutil.which("git")  # resolved, not a bare name off $PATH
    if git is None:
        return "unknown"
    try:
        # S603: fixed argv, no shell, no user input — the only variable is the
        # absolute path `which` resolved.
        return subprocess.run(  # noqa: S603
            [git, "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def check_against_baseline(metrics: dict[str, float]) -> list[str]:
    """Metrics that fell below the committed baseline, as readable lines."""
    if not BASELINE.exists():
        return []
    expected: dict[str, float] = json.loads(BASELINE.read_text(encoding="utf-8"))["metrics"]
    return [
        f"{name}: {metrics[name]:.3f} < {floor:.3f} baseline (down {floor - metrics[name]:.3f})"
        for name, floor in expected.items()
        if name in metrics and metrics[name] < floor - TOLERANCE
    ]


def record(entry: dict[str, object]) -> int:
    """Append one run to the trend log. JSON Lines: append-only, diff-friendly."""
    with HISTORY.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    return len(HISTORY.read_text(encoding="utf-8").splitlines())


def print_trend(limit: int = 15) -> None:
    if not HISTORY.exists():
        print(f"no history yet — run with --record to start one ({HISTORY.name})")
        return
    rows = [json.loads(line) for line in HISTORY.read_text(encoding="utf-8").splitlines() if line]
    print(f"{'when':<20} {'sha':<14} {'mode':<7} {'recall@1':>9} {'recall@k':>9} {'mrr':>6}")
    for row in rows[-limit:]:
        metrics: dict[str, float] = row["metrics"]
        recall_k = next(
            (v for name, v in metrics.items() if name.startswith("recall@") and name != "recall@1"),
            0.0,
        )
        print(
            f"{row['at'][:19]:<20} {row.get('sha', '?'):<14} {row.get('mode', '?'):<7} "
            f"{metrics.get('recall@1', 0):>9.2f} {recall_k:>9.2f} {metrics.get('mrr', 0):>6.2f}"
        )


async def run(
    k: int,
    collection: str | None,
    mode: RetrievalMode,
    rerank: bool,
    memory: bool,
    *,
    check: bool = False,
    record_run: bool = False,
    as_json: bool = False,
) -> int:
    settings = Settings()
    embedder = build_embedder(settings)

    # In --json mode stdout must carry exactly one JSON document, so every
    # human-facing line goes to stderr and stays pipeable either way.
    note = sys.stderr if as_json else sys.stdout

    if memory:
        client = AsyncQdrantClient(":memory:")
        target = collection or settings.qdrant_collection
        count = await ingest(CORPUS, settings, client=client, collection=target, recreate=True)
        print(f"(memory mode: ingested {count} chunks)", file=note)
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
        metrics = await evaluate(retriever, load_golden(), k, verbose=not as_json)
    finally:
        await client.close()

    entry: dict[str, object] = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sha": git_sha(),
        "embedder": embedder.model_id,
        "mode": mode,
        "rerank": rerank,
        "k": k,
        "questions": len(load_golden()),
        "metrics": {name: round(value, 4) for name, value in metrics.items()},
    }

    if as_json:
        print(json.dumps(entry, indent=2))
    else:
        print()
        print(f"embedder: {embedder.model_id}   mode: {mode}   rerank: {rerank}")
        print("   ".join(f"{name}: {value:.2f}" for name, value in metrics.items()))

    if record_run:
        print(f"recorded to {HISTORY.name} ({record(entry)} runs)", file=note)

    if check:
        regressions = check_against_baseline(metrics)
        if regressions:
            print("\nRETRIEVAL REGRESSION vs evals/baseline.json:", file=sys.stderr)
            for line in regressions:
                print(f"  - {line}", file=sys.stderr)
            print(
                "\nIf the drop is a deliberate trade-off, update baseline.json in "
                "the same commit and say why in the message.",
                file=sys.stderr,
            )
            return 1
        print("quality gate: OK (at or above evals/baseline.json)", file=note)
    return 0


def main() -> None:
    # The retrieval layer logs one line per query. Here those are diagnostics,
    # not results, so they go to stderr and stdout stays parseable — `--json`
    # in particular must be exactly one document.
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(sys.stderr))

    parser = argparse.ArgumentParser(description="Golden-set retrieval evaluation")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--collection", default=None, help="override ASSISTANT_QDRANT_COLLECTION")
    parser.add_argument("--mode", choices=["hybrid", "dense"], default="hybrid")
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument(
        "--memory", action="store_true", help="in-process Qdrant + on-the-fly ingest (no Docker)"
    )
    parser.add_argument(
        "--check", action="store_true", help="exit 1 if metrics fall below evals/baseline.json"
    )
    parser.add_argument(
        "--record", action="store_true", help="append this run to evals/history.jsonl"
    )
    parser.add_argument("--trend", action="store_true", help="print the recorded history and exit")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    if args.trend:
        print_trend()
        return

    raise SystemExit(
        asyncio.run(
            run(
                args.k,
                args.collection,
                cast("RetrievalMode", args.mode),
                not args.no_rerank,
                args.memory,
                check=args.check,
                record_run=args.record,
                as_json=args.json,
            )
        )
    )


if __name__ == "__main__":
    main()
