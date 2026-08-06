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


def _token_index(token: str) -> int:
    digest = hashlib.md5(token.encode()).digest()  # deterministic, not security
    return int.from_bytes(digest[:4], "little")


def encode_sparse(text: str) -> tuple[list[int], list[float]]:
    """Text -> (indices, values), summed on the rare index collision."""
    counts = Counter(_TOKEN_RE.findall(text.lower()))
    accumulated: dict[int, float] = {}
    for token, count in counts.items():
        index = _token_index(token)
        accumulated[index] = accumulated.get(index, 0.0) + 1.0 + math.log(count)
    indices = sorted(accumulated)
    return indices, [accumulated[index] for index in indices]
