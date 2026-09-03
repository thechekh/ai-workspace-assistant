"""Sparse lexical vectors for hybrid search.

Exact-token matching: each token maps to a stable 32-bit index (md5-based),
values are sublinear term frequencies. Unlike the 512-dim dense hash
embedder, the huge sparse index space makes collisions negligible — this is
the classic keyword-search signal, fused with dense similarity via RRF.
"""

import hashlib
import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"\w+")
# Inside an identifier: camelCase humps, ALLCAPS runs, digit runs. Applied to
# the ORIGINAL casing — lowercasing first would erase the camel boundary,
# which is exactly how `completedPercentage` was unfindable by "percentage"
# (observed live against an ingested React repo).
_SUBWORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def tokenize(text: str) -> list[str]:
    """Lowercased word tokens, plus the subwords of every code identifier.

    "completedPercentage / totalAmount" -> ["completedpercentage", "completed",
    "percentage", "totalamount", "total", "amount"] — the whole identifier
    stays searchable exactly, and each of its words matches natural-language
    queries. Shared by the sparse encoder, the relevance gate and the
    reranker so all three agree on what a token is.
    """
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text):
        tokens.append(raw.lower())
        parts = _SUBWORD_RE.findall(raw)
        if len(parts) > 1:
            tokens.extend(part.lower() for part in parts)
    return tokens


def _token_index(token: str) -> int:
    digest = hashlib.md5(token.encode(), usedforsecurity=False).digest()
    return int.from_bytes(digest[:4], "little")


def encode_sparse(text: str) -> tuple[list[int], list[float]]:
    """Text -> (indices, values), summed on the rare index collision."""
    counts = Counter(tokenize(text))
    accumulated: dict[int, float] = {}
    for token, count in counts.items():
        index = _token_index(token)
        accumulated[index] = accumulated.get(index, 0.0) + 1.0 + math.log(count)
    indices = sorted(accumulated)
    return indices, [accumulated[index] for index in indices]
