"""The documentation standard's mechanical half.

The rules live in docs/project/documentation-standard.md; each test names the
rule it enforces. Per-page checks run only on the pages in ADOPTED — a
ratchet: a page joins the list when it has been brought up to the standard
and can never fall back. The image checks run across all of docs/, because an
unlabeled or unused image is wrong anywhere.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"

# Grows one page at a time, in the same commit that brings the page up.
ADOPTED = [
    "reference/ragas.md",
    "reference/logfire-langfuse.md",
    "project/documentation-standard.md",
    "reference/tools.md",
    "reference/security.md",
    "reference/testing.md",
    "reference/metrics.md",
    "reference/backend-comparison.md",
    "reference/code-walkthrough.md",
    "reference/localhost.md",
    "theory/01-llm-basics.md",
    "theory/02-embeddings-and-vector-search.md",
    "theory/03-rag.md",
    "theory/04-tool-calling-and-agents.md",
    "theory/05-agent-frameworks.md",
    "theory/06-mcp.md",
    "theory/07-memory.md",
    "theory/08-realtime-websockets.md",
    "theory/09-observability-and-evals.md",
    "theory/10-infrastructure.md",
    "theory/11-glossary.md",
    "theory/12-defense-qa.md",
    "theory/README.md",
    "project/tech-stack.md",
    "project/implementation-plan.md",
    "project/learning-roadmap.md",
    "project/workshop.md",
    "project/demo-runbook.md",
    "project/future-tools.md",
    "project/description.md",
    "project/description-original.md",
    "handbook/06-tools-mcp.md",
    "handbook/07-observability.md",
    "handbook/08-agents-memory-ws.md",
    "handbook/09-testing-operations.md",
    "handbook/01-project-overview.md",
    "handbook/02-getting-started.md",
    "handbook/03-technologies.md",
    "handbook/04-llm-models-tokens.md",
    "handbook/05-rag-qdrant.md",
    "handbook/README.md",
]

H2 = re.compile(r"^## (.+?)\s*$", re.MULTILINE)
NUMBERED = re.compile(r"^(\d+)\. ")
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
REASONED_LINK = re.compile(r"^- \[[^\]]+\]\([^)]+\) — \S")
# A timestamp counts: "2026-09-04T00:19:17" says when as well as a bare date.
DATE = re.compile(r"\b20\d\d-\d\d-\d\d")
FENCED = re.compile(r"```.*?```", re.DOTALL)


def _text(page: str) -> str:
    return (DOCS / page).read_text(encoding="utf-8")


def _sections(text: str) -> list[tuple[str, str]]:
    """(title, body) for every H2, in order — ignoring headings quoted inside
    fenced code, such as the pasteable skeleton in the standard itself."""
    text = FENCED.sub("", text)
    matches = list(H2.finditer(text))
    return [
        (m.group(1), text[m.end() : matches[i + 1].start() if i + 1 < len(matches) else None])
        for i, m in enumerate(matches)
    ]


@pytest.mark.parametrize("page", ADOPTED)
def test_sections_are_numbered_and_contiguous(page: str) -> None:
    """Rule 2: numbered H2s, 1..n without gaps, so a reader learns the map once."""
    titles = [title for title, _ in _sections(_text(page))]
    numbers = [int(m.group(1)) for title in titles if (m := NUMBERED.match(title))]
    assert numbers == list(range(1, len(titles) + 1)), (
        f"{page}: H2 sections must be numbered 1..{len(titles)} in order; got {titles}"
    )


@pytest.mark.parametrize("page", ADOPTED)
def test_opens_with_a_bold_scope_paragraph(page: str) -> None:
    """Rule 1: the first paragraph under the title states the scope, in bold."""
    lines = _text(page).splitlines()
    assert lines[0].startswith("# "), f"{page}: first line must be the H1 title"
    first = next((line for line in lines[1:] if line.strip()), "")
    assert first.startswith("**"), (
        f"{page}: the paragraph under the title must open in bold, got {first[:60]!r}"
    )


@pytest.mark.parametrize("page", ADOPTED)
def test_ends_with_related_links_that_say_why(page: str) -> None:
    """Rule 14: the last section is Related, three to six links, each with a reason."""
    sections = _sections(_text(page))
    title, body = sections[-1]
    assert title.endswith("Related"), f"{page}: the last H2 must be 'Related', got {title!r}"
    links = [line for line in body.splitlines() if line.startswith("- [")]
    assert 3 <= len(links) <= 6, f"{page}: Related should hold 3-6 links, has {len(links)}"
    bare = [line for line in links if not REASONED_LINK.match(line)]
    assert not bare, f"{page}: every Related link needs ' — <why>' after it:\n  " + "\n  ".join(
        bare
    )


@pytest.mark.parametrize("page", ADOPTED)
def test_troubleshooting_table_has_symptom_cause_fix(page: str) -> None:
    """Rule 12: a Troubleshooting section is a Symptom … Fix table."""
    matching = [body for title, body in _sections(_text(page)) if "Troubleshooting" in title]
    if not matching:
        pytest.skip(f"{page} has no Troubleshooting section (rule 2 allows omitting it)")
    header = next((line for line in matching[0].splitlines() if line.startswith("| Symptom")), None)
    assert header is not None, (
        f"{page}: the Troubleshooting table must start with a 'Symptom' column"
    )
    assert header.rstrip().endswith("Fix |"), (
        f"{page}: the Troubleshooting table must end with a 'Fix' column"
    )


@pytest.mark.parametrize("page", ADOPTED)
def test_measured_numbers_carry_a_date(page: str) -> None:
    """Rule 4: a page that quotes measurements says when they were taken."""
    assert DATE.search(_text(page)), (
        f"{page}: no date on the page — when were its numbers measured?"
    )


def test_every_image_has_alt_text_and_is_used() -> None:
    """Rule 5: every capture is labelled, and every file in docs/images/ is used somewhere."""
    referenced: set[str] = set()
    unlabeled: list[str] = []
    for path in sorted(DOCS.rglob("*.md")):
        for alt, target in IMAGE.findall(path.read_text(encoding="utf-8")):
            if not alt.strip():
                unlabeled.append(f"{path.relative_to(REPO_ROOT).as_posix()}: {target}")
            resolved = (path.parent / target.split("#")[0]).resolve()
            referenced.add(resolved.as_posix())
    assert not unlabeled, "images without alt text:\n  " + "\n  ".join(unlabeled)

    orphans = [
        image.relative_to(REPO_ROOT).as_posix()
        for image in sorted((DOCS / "images").iterdir())
        if image.is_file() and image.resolve().as_posix() not in referenced
    ]
    assert not orphans, "images no page references (delete them or use them):\n  " + "\n  ".join(
        orphans
    )
