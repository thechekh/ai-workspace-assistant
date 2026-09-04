"""Generation quality on the golden set, judged by an LLM (Ragas).

`run_retrieval.py` measures whether the right chunk was *found* — recall@k and
MRR, deterministic and free. Nothing measured whether the answer the model then
wrote was actually *grounded* in that chunk. That gap was covered only by the
system prompt, the relevance gate, and a human reading the output.

**Faithfulness** closes it: the judge model splits the answer into individual
claims and checks each one against the retrieved context. 1.0 means every claim
is supported; 0.6 means four in ten were invented. That is hallucination,
measured.

A judge is only proven if it can *fail* something, so `--control` scores the
same answers a second time with fabricated claims appended. Clean answers at
0.9 could mean grounded answers or a judge that says yes to everything; the
poisoned copies must drop by the gap in `baseline.json`, or the headline number
means nothing. `--check` enforces both the floor and that gap, exit code 1.

Why this is a separate script and not part of the suite or the CI gate:

- Every metric is an LLM call, so it needs a key and costs money. The project's
  first rule is that a check requiring a key is a check that will not run.
- The scores are non-deterministic. `run_retrieval.py --check` compares against
  a baseline with a 0.005 tolerance; a judged score would flake against any
  threshold that tight. This gate is a *floor* with headroom, and `--record`
  keeps the trend in `history.jsonl`.
- `ragas` pulls ~35 packages. It lives in the optional `evals` dependency
  group so neither the image nor `uv sync` carries it.

Usage — in a Python 3.13 environment of its own, because ragas has no 3.14
wheels (scikit-network) and the main `.venv` should stay lean:

    UV_PROJECT_ENVIRONMENT=.venv-evals uv sync --python 3.13 --group evals   # once
    UV_PROJECT_ENVIRONMENT=.venv-evals uv run python -m evals.run_ragas --limit 3
    UV_PROJECT_ENVIRONMENT=.venv-evals uv run python -m evals.run_ragas --check --control --record

    PowerShell: $env:UV_PROJECT_ENVIRONMENT = ".venv-evals"; uv run python -m evals.run_ragas ...

Budget warning: faithfulness costs several LLM calls per question (extract the
claims, then verify each). All 18 questions is roughly 200 calls, and
`--control` doubles the judging. On `gpt-4.1-nano` a full run with the control
is a few cents, but start with `--limit 3` on a model you have not priced.
"""

import argparse
import asyncio
import json
import math
import sys
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean

import structlog
from qdrant_client import AsyncQdrantClient

from assistant.agent.backends.custom import CustomAgent
from assistant.agent.base import FinalEvent
from assistant.agent.tools import ToolRegistry, make_search_docs
from assistant.config import Settings
from assistant.llm.client import build_llm
from assistant.rag.embeddings import build_embedder
from assistant.rag.ingest import ingest
from assistant.rag.rerank import LexicalReranker
from assistant.rag.retriever import Retriever
from assistant.rag.store import VectorStore
from evals.run_retrieval import BASELINE, CORPUS, HISTORY, git_sha, load_golden

# Ragas' own field names — see docs.ragas.io. Kept as constants so the sample
# builder can be unit-tested without importing ragas at all.
USER_INPUT = "user_input"
RETRIEVED_CONTEXTS = "retrieved_contexts"
RESPONSE = "response"

METRIC = "faithfulness"

# The negative control. Three atomic claims no corpus document supports: the
# judge must mark every one unsupported, which drags a fully grounded k-claim
# answer down to k/(k+3). If it does not, the judge is broken, not the answers.
CONTROL_CLAIMS = (
    "This component was rewritten in Rust in 2031 by the Frankfurt platform team. "
    "It listens on port 9999 and is scheduled for removal after the next audit."
)


@dataclass(frozen=True)
class Sample:
    """One evaluated question, in the shape Ragas expects."""

    question: str
    contexts: list[str]
    answer: str

    def as_ragas(self) -> dict[str, object]:
        return {
            USER_INPUT: self.question,
            RETRIEVED_CONTEXTS: self.contexts,
            RESPONSE: self.answer,
        }


def build_dataset(samples: list[Sample]) -> list[dict[str, object]]:
    """Samples -> the list of dicts `EvaluationDataset.from_list` accepts.

    Separate from the collection step so it can be tested offline: this is the
    part that has to match Ragas' contract, and it needs no LLM to check.
    """
    return [sample.as_ragas() for sample in samples]


def usable(samples: list[Sample]) -> list[Sample]:
    """Drop samples a judge could not score.

    A question that retrieved nothing, or that the model declined to answer,
    has no claims to verify — scoring it would count an honest "I don't know"
    as a faithfulness failure, which is exactly backwards.
    """
    return [s for s in samples if s.contexts and s.answer.strip()]


def contaminate(samples: list[Sample]) -> list[Sample]:
    """The same questions and contexts, with fabricated claims appended to
    every answer. Contexts are untouched on purpose: the judge must find the
    new claims unsupported by the *same* evidence that supported the rest."""
    return [
        Sample(
            question=sample.question,
            contexts=sample.contexts,
            answer=f"{sample.answer.rstrip()} {CONTROL_CLAIMS}",
        )
        for sample in samples
    ]


def worst(
    samples: list[Sample], per_sample: list[dict[str, float]], *, n: int = 3
) -> list[tuple[float, Sample]]:
    """The lowest-scoring questions — where a human should look first."""
    scored = [
        (row[METRIC], sample)
        for row, sample in zip(per_sample, samples, strict=True)
        if METRIC in row and not math.isnan(row[METRIC])
    ]
    return sorted(scored, key=lambda pair: pair[0])[:n]


def check_judged(
    metrics: dict[str, float],
    control: dict[str, float] | None,
    rules: dict[str, dict[str, float]],
) -> list[str]:
    """Judged scores that violate baseline.json's `judged` rules, as readable lines.

    `floor` is a minimum for the clean run. `control_gap` is how far the
    poisoned run must fall below the clean one; it only applies when a control
    was run, and it is what proves the judge itself.
    """
    problems: list[str] = []
    for name, rule in rules.items():
        if name not in metrics:
            continue
        floor = rule.get("floor")
        if floor is not None and metrics[name] < floor:
            problems.append(f"{name}: {metrics[name]:.2f} < {floor:.2f} floor")
        gap = rule.get("control_gap")
        if control is not None and gap is not None and name in control:
            drop = metrics[name] - control[name]
            if drop < gap:
                problems.append(
                    f"{name}: fabricated claims lowered the score by only {drop:.2f} "
                    f"(clean {metrics[name]:.2f}, poisoned {control[name]:.2f}); "
                    f"the baseline requires a drop of at least {gap:.2f} — "
                    "the judge is not catching invented claims"
                )
    return problems


def load_judged_rules() -> dict[str, dict[str, float]]:
    return json.loads(Path(BASELINE).read_text(encoding="utf-8")).get("judged", {})


async def collect(settings: Settings, limit: int | None) -> list[Sample]:
    """Run the real agent over the golden questions and capture what it saw.

    In-process Qdrant with the corpus ingested on the fly, same as
    `run_retrieval.py --memory`, so the only external call is the model itself.
    """
    client = AsyncQdrantClient(":memory:")
    try:
        await ingest(CORPUS, settings, client=client, collection="ragas", recreate=True)
        retriever = Retriever(
            build_embedder(settings),
            VectorStore(client, "ragas"),
            mode=settings.retrieval_mode,
            reranker=LexicalReranker() if settings.rerank_enabled else None,
        )
        tools = ToolRegistry([make_search_docs(retriever)])
        agent = CustomAgent(
            llm=build_llm(settings), system_prompt=settings.system_prompt, tools=tools
        )

        questions = load_golden()[: limit or None]
        samples: list[Sample] = []
        for index, item in enumerate(questions, start=1):
            question = item["question"]
            # The contexts the judge scores against must be the ones the model
            # actually received, so retrieve once and reuse — not a second,
            # possibly different, search after the fact.
            hits = await retriever.search(question, limit=4)
            answer = ""
            async for event in agent.run(history=[], user_message=question):
                if isinstance(event, FinalEvent):
                    answer = event.content
            print(f"  [{index}/{len(questions)}] {question[:60]}", file=sys.stderr)
            samples.append(
                Sample(question=question, contexts=[hit.text for hit in hits], answer=answer)
            )
        return samples
    finally:
        await client.close()


def _finite(value: object) -> bool:
    return isinstance(value, int | float) and not math.isnan(value)


def judge(
    dataset: list[dict[str, object]], settings: Settings
) -> tuple[dict[str, float], list[dict[str, float]]]:
    """Score the dataset with Ragas: (aggregate, per-sample) scores.

    Imported here so the module loads without ragas installed.
    """
    # Deferred, and unresolvable to the type checker by design: these live in
    # the optional `evals` group, so a normal `uv sync` (and CI) never has them.
    # ragas 0.4 deprecates this classic `evaluate()` API in favour of per-sample
    # metric collections; the `ragas<1.0` pin in pyproject keeps it, and the
    # warnings would otherwise bury the scores in the output.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from langchain_openai import ChatOpenAI  # pyright: ignore[reportMissingImports]
        from ragas import EvaluationDataset, evaluate  # pyright: ignore[reportMissingImports]
        from ragas.llms import LangchainLLMWrapper  # pyright: ignore[reportMissingImports]
        from ragas.metrics import Faithfulness  # pyright: ignore[reportMissingImports]

        from assistant.llm.client import resolve_provider

        api_key, base_url = resolve_provider(settings)
        # Ragas talks OpenAI; every provider this project supports is
        # OpenAI-compatible, so the judge is the configured model by default.
        judge_llm = ChatOpenAI(model=settings.llm_model, api_key=api_key, base_url=base_url)

        result = evaluate(
            dataset=EvaluationDataset.from_list(dataset),
            # Faithfulness only: it is reference-free (the golden set stores
            # where an answer lives, not the answer itself) and needs no
            # embeddings, which keeps this runnable against any
            # OpenAI-compatible endpoint, including ones with no embeddings API.
            metrics=[Faithfulness()],
            llm=LangchainLLMWrapper(judge_llm),
        )
    per_sample = [
        {name: float(value) for name, value in row.items() if _finite(value)}
        for row in getattr(result, "scores", [])
    ]
    if per_sample:
        names = {name for row in per_sample for name in row}
        aggregate = {
            name: fmean(row[name] for row in per_sample if name in row) for name in sorted(names)
        }
    else:  # older result objects expose only the aggregate
        scores = result._repr_dict if hasattr(result, "_repr_dict") else dict(result)
        aggregate = {name: float(value) for name, value in scores.items() if _finite(value)}
    return aggregate, per_sample


def main() -> None:
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(sys.stderr))

    parser = argparse.ArgumentParser(description="LLM-judged generation quality (Ragas)")
    parser.add_argument("--limit", type=int, default=None, help="score only the first N questions")
    parser.add_argument(
        "--control",
        action="store_true",
        help="also judge the answers with fabricated claims appended; proves the judge",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 below the `judged` floor in evals/baseline.json (and control gap)",
    )
    parser.add_argument("--record", action="store_true", help="append to evals/history.jsonl")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    settings = Settings()
    if settings.llm_provider == "fake":
        raise SystemExit(
            "ragas judges with a real model — set ASSISTANT_LLM_PROVIDER and a key.\n"
            "For a free, deterministic, offline check use: "
            "uv run python evals/run_retrieval.py --memory"
        )

    note = sys.stderr if args.json else sys.stdout
    print(f"collecting answers ({settings.llm_model})...", file=note)
    samples = usable(asyncio.run(collect(settings, args.limit)))
    if not samples:
        raise SystemExit("no answerable questions collected — is the corpus indexed?")

    print(f"judging {len(samples)} answers...", file=note)
    metrics, per_sample = judge(build_dataset(samples), settings)
    control: dict[str, float] | None = None
    if args.control:
        print(f"judging {len(samples)} poisoned copies (negative control)...", file=note)
        control, _ = judge(build_dataset(contaminate(samples)), settings)

    entry: dict[str, object] = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sha": git_sha(),
        "suite": "ragas",
        "judge_model": settings.llm_model,
        "questions": len(samples),
        "metrics": {name: round(value, 4) for name, value in metrics.items()},
    }
    if control is not None:
        entry["control"] = {name: round(value, 4) for name, value in control.items()}

    lowest = worst(samples, per_sample)
    if args.json:
        print(
            json.dumps(
                {
                    **entry,
                    "per_question": [
                        {"question": sample.question, **row}
                        for sample, row in zip(samples, per_sample, strict=True)
                    ],
                },
                indent=2,
            )
        )
    else:
        print()
        print(f"judge: {settings.llm_model}   questions: {len(samples)}")
        print("   ".join(f"{name}: {value:.2f}" for name, value in metrics.items()))
        if control is not None:
            print(
                "   ".join(
                    f"{name} with fabricated claims: {value:.2f}" for name, value in control.items()
                )
            )
        if lowest:
            print("lowest-scoring questions:")
            for score, sample in lowest:
                print(f"  {score:.2f}  {sample.question}")

    # Record before gating: a failing run is exactly the one the trend log
    # must keep.
    if args.record:
        with Path(HISTORY).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        print(f"recorded to {Path(HISTORY).name}", file=note)

    if args.check:
        problems = check_judged(metrics, control, load_judged_rules())
        if problems:
            print("\nJUDGED QUALITY BELOW evals/baseline.json:", file=sys.stderr)
            for line in problems:
                print(f"  {line}", file=sys.stderr)
            raise SystemExit(1)
        print("judge gate: OK (floor and control gap in evals/baseline.json)", file=note)


if __name__ == "__main__":
    main()
