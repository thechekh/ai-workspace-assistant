"""Chunking source code by lines, and Markdown headings inside code fences.

Found by ingesting a repository with include_code=true: every top-level
`# comment` in a Python file became a "heading", so a source file was cut at
each comment and its chunks were labelled with comment text.
"""

from assistant.rag.chunking import chunk_code, chunk_document, chunk_markdown
from assistant.rag.filetypes import is_code_path, is_doc_path

PYTHON = '''"""Module docstring."""

# load the config
import os

# helper
def load() -> str:
    return os.environ.get("X", "")
'''


def test_code_is_not_split_at_comment_lines():
    chunks = chunk_document(PYTHON, source="acme/app/config.py")
    assert len(chunks) == 1
    [chunk] = chunks
    assert chunk.heading == "lines 1-8"
    assert chunk.text.startswith("acme/app/config.py\n\n")
    assert "# load the config" in chunk.text
    assert "load the config" not in chunk.heading


def test_markdown_routes_to_the_heading_chunker():
    chunks = chunk_document("# Title\n\nbody\n\n## Sub\n\nmore", source="README.md")
    assert [chunk.heading for chunk in chunks] == ["Title", "Title > Sub"]


def test_chunk_code_cuts_on_whole_lines_with_stable_ids():
    lines = [f"line {i} " + "x" * 40 for i in range(200)]
    chunks = chunk_code("\n".join(lines), source="a.py", target_chars=1000)
    assert len(chunks) > 1
    # No line is split across chunks, and the ranges tile the file.
    ranges = [chunk.heading.removeprefix("lines ") for chunk in chunks]
    starts = [int(r.split("-")[0]) for r in ranges]
    ends = [int(r.split("-")[1]) for r in ranges]
    assert starts[0] == 1
    assert ends[-1] == 200
    assert all(end + 1 == nxt for end, nxt in zip(ends, starts[1:], strict=False))
    # Deterministic: the same file gives the same ids; another file does not.
    again = chunk_code("\n".join(lines), source="a.py", target_chars=1000)
    assert [c.id for c in again] == [c.id for c in chunks]
    other = chunk_code("\n".join(lines), source="b.py", target_chars=1000)
    assert [c.id for c in other] != [c.id for c in chunks]


def test_chunk_code_splits_a_single_oversized_line():
    chunks = chunk_code("x" * 5000, source="blob.js", hard_limit=2400)
    assert len(chunks) == 3
    assert all(len(chunk.text) <= 2400 + len("blob.js\n\n") for chunk in chunks)


def test_headings_inside_fenced_code_are_not_headings():
    md = "# Guide\n\n```python\n# not a heading\nx = 1\n```\n\ntext after\n"
    chunks = chunk_markdown(md, source="g.md")
    assert [chunk.heading for chunk in chunks] == ["Guide"]
    assert "# not a heading" in chunks[0].text


def test_suffix_helpers_agree_on_what_is_code_and_what_is_prose():
    assert is_code_path("src/app.py")
    assert is_code_path("infra/deploy.YAML")
    assert is_doc_path("README.md")
    assert is_doc_path("notes.markdown")
    assert not is_doc_path("image.png")
    assert not is_code_path("README.md")
