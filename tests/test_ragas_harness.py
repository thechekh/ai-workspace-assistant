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
