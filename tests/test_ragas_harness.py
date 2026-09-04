"""The Ragas harness, tested without Ragas and without an LLM.

Judging needs a real model, so the scoring itself cannot be tested here — that
is the whole reason it lives outside the suite. What *can* be tested is
everything around it, and that is where the bugs would be: the dataset must
match Ragas' field contract exactly, and unanswerable questions must be
dropped before they reach the judge.

The field names are asserted as literal strings on purpose. If Ragas renames
`user_input`, this fails with a clear message instead of the runner producing
an empty score two hundred LLM calls later.
"""

import pytest
from evals.run_ragas import Sample, build_dataset, usable

_CONTEXTS = ["billing-service generates PDF invoices nightly."]


def test_the_dataset_matches_ragas_field_names() -> None:
    dataset = build_dataset(
        [Sample(question="Which service bills?", contexts=_CONTEXTS, answer="billing-service.")]
    )

    assert dataset == [
        {
            "user_input": "Which service bills?",
            "retrieved_contexts": _CONTEXTS,
            "response": "billing-service.",
        }
    ]


def test_contexts_stay_a_list_of_plain_strings() -> None:
    """Ragas expects `list[str]`; passing RetrievedChunk objects would blow up
    inside the judge rather than here."""
    [row] = build_dataset([Sample(question="q", contexts=["chunk one", "chunk two"], answer="a")])
    contexts = row["retrieved_contexts"]
    assert isinstance(contexts, list)
    assert all(isinstance(context, str) for context in contexts)


@pytest.mark.parametrize(
    ("contexts", "answer", "kept"),
    [
        (_CONTEXTS, "a real answer", True),
        # Retrieval found nothing: there is no context to check claims against.
        ([], "a real answer", False),
        # The model declined. Scoring this would mark an honest "I don't know"
        # as unfaithful — the opposite of what the metric is for.
        (_CONTEXTS, "", False),
        (_CONTEXTS, "   \n  ", False),
    ],
)
def test_unscorable_samples_are_dropped(contexts: list[str], answer: str, kept: bool) -> None:
    samples = [Sample(question="q", contexts=contexts, answer=answer)]
    assert bool(usable(samples)) is kept


def test_a_mixed_batch_keeps_only_the_scorable_ones() -> None:
    samples = [
        Sample(question="answered", contexts=_CONTEXTS, answer="yes"),
        Sample(question="nothing retrieved", contexts=[], answer="yes"),
        Sample(question="declined", contexts=_CONTEXTS, answer=""),
    ]
    assert [sample.question for sample in usable(samples)] == ["answered"]


def test_the_runner_refuses_the_fake_provider() -> None:
    """`fake` would score a scripted echo and report a meaningless number.

    The message has to point at the free alternative, because "you need a key"
    is only half an answer when a deterministic retrieval eval already exists.
    """
    import evals.run_ragas as runner

    source = runner.__file__ and __import__("pathlib").Path(runner.__file__).read_text(
        encoding="utf-8"
    )
    assert 'settings.llm_provider == "fake"' in source
    assert "run_retrieval.py --memory" in source, (
        "the refusal should name the offline eval that does work"
    )


def test_the_control_poisons_answers_but_not_evidence() -> None:
    """The negative control must change only the thing under test."""
    from evals.run_ragas import CONTROL_CLAIMS, contaminate

    clean = [Sample(question="Which service bills?", contexts=_CONTEXTS, answer="billing-service.")]
    [poisoned] = contaminate(clean)

    assert poisoned.question == clean[0].question
    assert poisoned.contexts == clean[0].contexts
    assert poisoned.answer.startswith("billing-service.")
    assert poisoned.answer.endswith(CONTROL_CLAIMS)
    # The invented claims must be unsupported by the evidence, or the control
    # proves nothing: no word of them may appear in any context.
    assert all(
        word.lower() not in " ".join(_CONTEXTS).lower() for word in ("Rust", "9999", "Frankfurt")
    )


def test_worst_lists_the_lowest_scores_first_and_skips_unscored() -> None:
    from evals.run_ragas import worst

    samples = [
        Sample(question=q, contexts=_CONTEXTS, answer="a") for q in ("high", "low", "nan", "mid")
    ]
    scores = [{"faithfulness": 1.0}, {"faithfulness": 0.2}, {}, {"faithfulness": 0.6}]

    assert [(score, sample.question) for score, sample in worst(samples, scores, n=2)] == [
        (0.2, "low"),
        (0.6, "mid"),
    ]


@pytest.mark.parametrize(
    ("metrics", "control", "expected_fragments"),
    [
        # Clean run above the floor, control far below: the judge is proven.
        ({"faithfulness": 0.92}, {"faithfulness": 0.55}, []),
        # Below the floor: generation regressed (or the judge got stricter).
        ({"faithfulness": 0.71}, None, ["0.71 < 0.80 floor"]),
        # Control barely moved: the judge says yes to everything.
        ({"faithfulness": 0.95}, {"faithfulness": 0.90}, ["not catching invented claims"]),
        # No control run: only the floor applies.
        ({"faithfulness": 0.85}, None, []),
        # A metric without a rule is ignored rather than failed.
        ({"faithfulness": 0.85, "other": 0.1}, None, []),
    ],
)
def test_check_judged_enforces_floor_and_control_gap(
    metrics: dict[str, float], control: dict[str, float] | None, expected_fragments: list[str]
) -> None:
    from evals.run_ragas import check_judged

    rules = {"faithfulness": {"floor": 0.80, "control_gap": 0.20}}
    problems = check_judged(metrics, control, rules)

    assert len(problems) == len(expected_fragments)
    for fragment, problem in zip(expected_fragments, problems, strict=True):
        assert fragment in problem


def test_the_committed_baseline_has_judged_rules() -> None:
    """`--check` reads these; a missing section would silently pass everything."""
    import json
    from pathlib import Path

    baseline = json.loads(Path("evals/baseline.json").read_text(encoding="utf-8"))
    rules = baseline["judged"]["faithfulness"]
    assert 0.5 <= rules["floor"] <= 1.0
    assert 0.05 <= rules["control_gap"] <= 0.5
