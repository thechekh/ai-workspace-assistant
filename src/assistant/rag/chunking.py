"""Heading-aware Markdown chunking.

Documents are split along their heading structure; each chunk carries the
heading breadcrumb ("Service Catalog > billing-service") both as metadata and
as a prefix of the embedded text — a cheap, effective retrieval boost.

Chunk ids are deterministic (uuid5 of source + breadcrumb + index), so
re-ingesting the same corpus overwrites points in place instead of
duplicating them.
"""

import re
import uuid

from pydantic import BaseModel

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


class Chunk(BaseModel):
    id: str
    text: str  # breadcrumb + body — this is what gets embedded
    source: str  # corpus-relative file path
    heading: str  # breadcrumb, e.g. "Service Catalog > billing-service"
    index: int  # position within the document


def _split_paragraphs(body: str) -> list[str]:
    """Split on blank lines, keeping fenced code blocks intact."""
    parts: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            current.append(line)
            continue
        if not line.strip() and not in_fence:
            if current:
                parts.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        parts.append("\n".join(current))
    return parts


def _pack(paragraphs: list[str], target: int, hard: int) -> list[str]:
    """Greedily pack paragraphs up to ~target chars; hard-split oversized ones."""
    pieces: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > hard:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(
                paragraph[start : start + hard] for start in range(0, len(paragraph), hard)
            )
            continue
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > target and current:
            pieces.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def chunk_markdown(
    markdown: str,
    *,
    source: str,
    target_chars: int = 1800,  # ~450 tokens
    hard_limit: int = 2400,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    heading_stack: dict[int, str] = {}
    section_lines: list[str] = []

    def flush_section() -> None:
        body = "\n".join(section_lines).strip()
        section_lines.clear()
        if not body:
            return
        breadcrumb = " > ".join(title for _, title in sorted(heading_stack.items()))
        for piece_index, piece in enumerate(
            _pack(_split_paragraphs(body), target_chars, hard_limit)
        ):
            chunk_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{source}::{breadcrumb}::{piece_index}")
            text = f"{breadcrumb}\n\n{piece}" if breadcrumb else piece
            chunks.append(
                Chunk(
                    id=str(chunk_id),
                    text=text,
                    source=source,
                    heading=breadcrumb,
                    index=len(chunks),
                )
            )

    for line in markdown.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            flush_section()  # content so far belongs to the previous heading
            level = len(match.group(1))
            heading_stack[level] = match.group(2).strip()
            for deeper in [lvl for lvl in heading_stack if lvl > level]:
                del heading_stack[deeper]
        else:
            section_lines.append(line)
    flush_section()
    return chunks
