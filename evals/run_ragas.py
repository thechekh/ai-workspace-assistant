"""Generation quality on the golden set, judged by an LLM (Ragas).

`run_retrieval.py` measures whether the right chunk was *found* — recall@k and
MRR, deterministic and free. Nothing measured whether the answer the model then
wrote was actually *grounded* in that chunk. That gap was covered only by the
system prompt, the relevance gate, and a human reading the output.

**Faithfulness** closes it: the judge model splits the answer into individual
claims and checks each one against the retrieved context. 1.0 means every claim
is supported; 0.6 means four in ten were invented. That is hallucination,
measured.

Why this is a separate script and not part of the suite or the CI gate:

- Every metric is an LLM call, so it needs a key and costs money. The project's
  first rule is that a check requiring a key is a check that will not run.
- The scores are non-deterministic. `run_retrieval.py --check` compares against
  a baseline with a 0.005 tolerance; a judged score would flake against any
  threshold that tight. This records a trend instead of gating a build.
- `ragas` pulls ~35 packages. It lives in the optional `evals` dependency
  group so neither the image nor `uv sync` carries it.

Usage:
    uv sync --group evals
    uv run python -m evals.run_ragas [--limit 5] [--record]

Needs Python 3.13 or older: ragas depends on scikit-network, which publishes
wheels for cp310-cp313 only, so on 3.14 uv falls back to building it from
source and wants a C++ toolchain. The Docker image ships 3.13, and CI tests
3.12 and 3.13, so this is only a constraint for a 3.14 dev machine.

Budget warning: faithfulness costs several LLM calls per question (extract the
claims, then verify each). All 18 questions is roughly 200 calls. On
`gpt-4.1-nano` that is fractions of a cent, but start with `--limit 3` on a
model you have not priced.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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
from evals.run_retrieval import CORPUS, HISTORY, git_sha, load_golden

# Ragas' own field names — see docs.ragas.io. Kept as constants so the sample
# builder can be unit-tested without importing ragas at all.
USER_INPUT = "user_input"
RETRIEVED_CONTEXTS = "retrieved_contexts"
RESPONSE = "response"


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


def judge(dataset: list[dict[str, object]], settings: Settings) -> dict[str, float]:
    """Score the dataset with Ragas. Imported here so the module loads without it."""
    # Deferred, and unresolvable to the type checker by design: these live in
    # the optional `evals` group, so a normal `uv sync` (and CI) never has them.
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
        # Faithfulness only: it is reference-free (the golden set stores where
        # an answer lives, not the answer itself) and needs no embeddings,
        # which keeps this runnable against a provider that serves no
        # embeddings endpoint.
        metrics=[Faithfulness()],
        llm=LangchainLLMWrapper(judge_llm),
    )
    scores = result._repr_dict if hasattr(result, "_repr_dict") else dict(result)
    return {name: float(value) for name, value in scores.items() if isinstance(value, int | float)}


def main() -> None:
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(sys.stderr))

    parser = argparse.ArgumentParser(description="LLM-judged generation quality (Ragas)")
    parser.add_argument("--limit", type=int, default=None, help="score only the first N questions")
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
    print(f"collecting answers ({settings.llm_model})…", file=note)
    samples = usable(asyncio.run(collect(settings, args.limit)))
    if not samples:
        raise SystemExit("no answerable questions collected — is the corpus indexed?")

    print(f"judging {len(samples)} answers…", file=note)
    metrics = judge(build_dataset(samples), settings)

    entry: dict[str, object] = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sha": git_sha(),
        "suite": "ragas",
        "judge_model": settings.llm_model,
        "questions": len(samples),
        "metrics": {name: round(value, 4) for name, value in metrics.items()},
    }

    if args.json:
        print(json.dumps(entry, indent=2))
    else:
        print()
        print(f"judge: {settings.llm_model}   questions: {len(samples)}")
        print("   ".join(f"{name}: {value:.2f}" for name, value in metrics.items()))

    if args.record:
        with Path(HISTORY).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        print(f"recorded to {Path(HISTORY).name}", file=note)


if __name__ == "__main__":
    main()
