"""Rerankers: reorder the top retrieval candidates before answering.

`LexicalReranker` is deterministic and offline (token overlap, length
normalized) — the no-envs default. API rerankers (voyage rerank-2, Cohere)
implement the same protocol and slot in via config once keys exist.
"""

import re
from typing import Protocol

from assistant.rag.store import RetrievedChunk

_TOKEN_RE = re.compile(r"\w+")
_STOPWORDS = frozenset(
    [
        *("a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in"),
        *("is", "it", "of", "on", "or", "our", "the", "to", "we", "what", "which", "who"),
    ]
)


def query_overlap(query: str, text: str) -> int:
    """How many meaningful query tokens appear in `text` (prefix-tolerant:
    "deploy" matches "deployment"). 0 means the chunk is unrelated to the
    query — retrieval scores are not calibrated (RRF/hash embeddings), so
    this is the relevance gate used by the search_docs tool."""
    query_tokens = {token for token in _TOKEN_RE.findall(query.lower()) if token not in _STOPWORDS}
    if not query_tokens:
        return 1  # nothing meaningful to gate on — let the chunk through
    text_tokens = set(_TOKEN_RE.findall(text.lower()))
    overlap = 0
    for token in query_tokens:
        if token in text_tokens or (
            len(token) >= 4
            and any(
                candidate.startswith(token) or token.startswith(candidate)
                for candidate in text_tokens
                if len(candidate) >= 4
            )
        ):
            overlap += 1
    return overlap


class Reranker(Protocol):
    def rerank(
        self, query: str, candidates: list[RetrievedChunk], *, limit: int
    ) -> list[RetrievedChunk]: ...


class LexicalReranker:
    def rerank(
        self, query: str, candidates: list[RetrievedChunk], *, limit: int
    ) -> list[RetrievedChunk]:
        query_tokens = {
            token for token in _TOKEN_RE.findall(query.lower()) if token not in _STOPWORDS
        }
        if not query_tokens:
            return candidates[:limit]

        def score(chunk: RetrievedChunk) -> float:
            chunk_tokens = set(_TOKEN_RE.findall(chunk.text.lower()))
            overlap = len(query_tokens & chunk_tokens)
            # Overlap dominates; the retrieval score breaks ties deterministically.
            return overlap + min(chunk.score, 0.999) / 1000

        return sorted(candidates, key=score, reverse=True)[:limit]
