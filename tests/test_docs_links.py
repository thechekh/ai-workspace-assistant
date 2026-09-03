"""The documentation is a single source of truth — keep it internally valid.

All prose docs live under `docs/` (plus the root README). These tests fail if
a relative link points at something that no longer exists, which is what
happens when files get moved or renamed.

Deliberately excluded: `docs_corpus/` (RAG *data*, referenced by path from
code) and generated artifacts.
"""

import posixpath
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {
    "node_modules",
    ".venv",
    ".playwright-mcp",
    ".git",
    "htmlcov",
    # tool caches — gitignored, and some ship their own README
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    "build",
}
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _markdown_files() -> list[Path]:
    return sorted(
        path
        for path in REPO_ROOT.rglob("*.md")
        if not SKIP_DIRS & set(path.relative_to(REPO_ROOT).parts)
    )


def _relative_links(path: Path) -> list[str]:
    """Link targets that point at files on disk (not the web, not anchors)."""
    targets = []
    for raw in LINK_RE.findall(path.read_text(encoding="utf-8")):
        if raw.startswith(("http://", "https://", "#", "mailto:")):
            continue
        target = raw.split("#")[0]
        if target:
            targets.append(target)
    return targets


@pytest.mark.parametrize("md", _markdown_files(), ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_relative_links_resolve(md: Path):
    base = md.parent.relative_to(REPO_ROOT).as_posix()
    broken = []
    for target in _relative_links(md):
        joined = posixpath.join(base, target) if base != "." else target
        if not (REPO_ROOT / posixpath.normpath(joined)).exists():
            broken.append(target)
    assert not broken, f"{md.relative_to(REPO_ROOT).as_posix()} has broken links: {broken}"


def test_all_prose_docs_live_under_docs():
    """One source of truth: no stray .md outside docs/ except the allowed few."""
    # GitHub looks for these at the repository root by convention, so they
    # cannot move into docs/ — each is a short pointer into the docs tree.
    allowed = {
        "README.md",  # landing page + packaging readme
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CHANGELOG.md",
        "CLAUDE.md",  # instructions for AI coding agents
        "evals/results-embeddings.md",  # generated next to its generator
    }
    # evals/corpus/ is the retrieval test fixture (the golden set's answers
    # live in it), not documentation — same reason docs_corpus/ was exempt.
    allowed_prefixes = ("docs/", "evals/corpus/")
    strays = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in _markdown_files()
        if not path.relative_to(REPO_ROOT).as_posix().startswith(allowed_prefixes)
        and path.relative_to(REPO_ROOT).as_posix() not in allowed
    ]
    assert not strays, f"move these into docs/ (or allow-list them): {strays}"


def test_docs_index_links_every_subfolder():
    index = (REPO_ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    for folder in ("handbook/", "theory/", "reference/", "project/", "qanda/"):
        assert folder in index, f"docs/README.md does not mention {folder}"
