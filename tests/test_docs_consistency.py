"""Facts stated in the docs must match the code.

Several numbers are quoted in many documents at once — the golden-set
results appear in seven files, the backend line counts in five. That
readability is worth keeping (a chapter that makes you click elsewhere for
its own headline number is worse), but it drifts: an audit found test counts
claimed as 72, 129, 145 and 203 simultaneously, contradicting each other
inside single files.

So the duplication stays and these tests hold it together: every copy must
agree with every other copy, and with reality.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_FILES = [*(REPO_ROOT / "docs").rglob("*.md"), REPO_ROOT / "README.md"]
# Suite-size claims also live in the root CLAUDE.md, which sat two hundred
# tests out of date because nothing scanned it.
COUNT_CLAIM_FILES = [*DOC_FILES, REPO_ROOT / "CLAUDE.md"]

# Word-boundary matching matters: a substring test for "line" also matches
# "timeline", which made an HTTP status list (429/401/404) look like a
# line-count claim.
_SIZE_CONTEXT = re.compile(r"\b(lines?|loc)\b", re.IGNORECASE)
_TRIPLE = re.compile(r"\b(\d{2,4})\s*/\s*(\d{2,4})\s*/\s*(\d{2,4})\b")


def _all_docs() -> list[tuple[Path, str]]:
    return [(path, path.read_text(encoding="utf-8")) for path in DOC_FILES]


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def test_backend_line_counts_match_the_source() -> None:
    """`98 / 194 / 278` is quoted in several docs — keep every copy true."""
    actual = tuple(
        str(
            len(
                (REPO_ROOT / "src/assistant/agent/backends" / f"{name}.py")
                .read_text(encoding="utf-8")
                .splitlines()
            )
        )
        for name in ("custom", "pydantic_ai", "langgraph")
    )

    wrong = []
    for path, text in _all_docs():
        for match in _TRIPLE.finditer(text):
            window = text[max(0, match.start() - 100) : match.end() + 60]
            if _SIZE_CONTEXT.search(window) and match.groups() != actual:
                wrong.append(f"{_rel(path)}: {match.group(0)} (actual {'/'.join(actual)})")
    assert not wrong, "backend LoC claims are stale:\n  " + "\n  ".join(wrong)


def test_golden_set_question_count_matches_the_file() -> None:
    """Docs say "18-question golden set" in several places."""
    golden = yaml.safe_load((REPO_ROOT / "evals/golden.yaml").read_text(encoding="utf-8"))
    count = len(golden["questions"] if isinstance(golden, dict) else golden)

    claims = [
        (_rel(path), int(match.group(1)))
        for path, text in _all_docs()
        for match in re.finditer(r"(\d+)[- ]question golden set", text)
    ]
    assert claims, "no document states the golden-set size — that claim should exist"
    wrong = [f"{where}: {n}" for where, n in claims if n != count]
    assert not wrong, f"golden set actually has {count} questions; stale claims:\n  " + "\n  ".join(
        wrong
    )


def test_retrieval_scores_do_not_contradict_each_other() -> None:
    """recall@1 is quoted in ~7 files; only the measured values are valid.

    An audit caught the 0.56/0.67 ablation rows still being quoted as current
    long after Phase 8's relevance gate had moved them — the headline 0.83 was
    unchanged, which is exactly why nobody noticed. The dated acceptance
    records in docs/project/ keep the old numbers on purpose and are skipped.
    """
    # default hybrid+rerank, dense+rerank, dense-only, hybrid-only, and
    # text-embedding-3-small (the measured semantic embedder)
    measured = {"0.83", "0.89", "0.78", "0.72", "0.94"}
    historical = {"0.56", "0.67"}  # Phase 2/7, superseded and labelled as such
    contradictions = [
        f"{_rel(path)}: recall@1 {match.group(1)}"
        for path, text in _all_docs()
        # Dated acceptance records state what was true when they were written.
        if "project/implementation-plan" not in _rel(path)
        # `(?<![+-])` so a stated *delta* ("+0.11 recall@1") is not read as a
        # claim about the metric's value.
        for match in re.finditer(r"recall@1[^\n]*?(?<![+-])(\d\.\d\d)", text)
        if match.group(1) not in measured | historical
    ]
    assert not contradictions, (
        f"recall@1 claims disagree with the measured {sorted(measured)}:\n  "
        + "\n  ".join(contradictions)
    )
    headline = re.compile(r"0\.83\*{0,2}\s*/\s*\*{0,2}1\.00\*{0,2}\s*/\s*\*{0,2}0\.92")
    assert any(headline.search(text) for _, text in _all_docs()), (
        "no document states the headline 0.83/1.00/0.92 result"
    )


@pytest.mark.slow
def test_test_count_claims_agree_and_are_not_badly_stale() -> None:
    """Suite-size claims must agree with each other and be roughly current.

    Deliberately a tolerance rather than equality: requiring an exact match
    would mean every added test breaks the build until five documents are
    edited. What actually hurt was contradiction (72 vs 129 vs 145 at once)
    and gross staleness, so that is what this catches.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "--no-cov",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    match = re.search(r"(\d+) tests collected", result.stdout)
    assert match, f"could not read the collected count from pytest:\n{result.stdout[-500:]}"
    actual = int(match.group(1))

    # Historical acceptance records are dated evidence, not current claims,
    # and "not slow" subset counts are a different number by design.
    skip_line = re.compile(r"\d+/\d+ tests green|not slow")
    # An adjective between the number and "tests" used to hide the claim
    # entirely: "129 deterministic tests" and "212 Python tests" both drifted
    # for months because the pattern demanded the two words be adjacent.
    # `\s+` rather than a literal space: prose wraps, and "**342\noffline
    # tests**" hid a stale claim from a line-by-line scan.
    # The hyphenated form ("129-test suite") hid five more stale claims from
    # every earlier version of this scan.
    claim_re = re.compile(r"\b(\d{2,4})(?:\s+((?:\w+\s+){0,2}?)tests\b|-test\b)")
    # The frontend suite is a different number by design, not a contradiction.
    other_suite = re.compile(r"\b(frontend|vitest|npm|ui)\b", re.IGNORECASE)
    claims: list[tuple[str, int]] = []
    for path in COUNT_CLAIM_FILES:
        text = path.read_text(encoding="utf-8")
        for claim in claim_re.finditer(text):
            line_start = text.rfind("\n", 0, claim.start()) + 1
            line_end = text.find("\n", claim.start())
            line = text[line_start : line_end if line_end != -1 else None]
            if skip_line.search(line) or other_suite.search(claim.group(2) or ""):
                continue
            claims.append((_rel(path), int(claim.group(1))))
    assert claims, "no document states the suite size — that claim should exist"

    distinct = {n for _, n in claims}
    assert len(distinct) == 1, (
        "documents disagree with each other about the suite size "
        f"({sorted(distinct)}):\n  " + "\n  ".join(f"{w}: {n}" for w, n in claims)
    )

    claimed = distinct.pop()
    drift = abs(actual - claimed) / actual
    assert drift <= 0.05, (
        f"the suite has {actual} tests but the docs say {claimed} ({drift:.0%} off) — update them"
    )
