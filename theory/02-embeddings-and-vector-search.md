# 02 — Embeddings & vector search

## The problem: computers can't compare meaning

"How do we ship to prod?" and "What's our deployment process?" share almost
no words, yet mean the same thing. Keyword search (SQL `LIKE`, grep) sees
zero overlap. We need a way to compare **meaning**, not spelling.

## Embeddings: text → a point in space

An **embedding model** turns a piece of text into a vector — a list of
numbers, e.g. 1536 floats. The model is trained so that **texts with similar
meaning land close together** in that space. "ship to prod" and "deployment
process" end up as nearby points; "lunch menu" ends up far away.

Similarity between two vectors is measured with **cosine similarity** — the
angle between them: 1.0 = same direction (same meaning), 0 = unrelated.
"Find relevant docs" becomes geometry: *embed the question, find the nearest
stored vectors.*

## Vector databases, and why Qdrant

Comparing a query against millions of vectors needs a purpose-built store
with approximate-nearest-neighbor indexes (HNSW is the standard algorithm)
— that's a **vector database**. We chose **Qdrant**: single container,
first-class async Python client, payload filters (each vector carries JSON
metadata — we filter by `source` file), named vectors (below), and native
hybrid-search support. Alternatives we considered: pgvector (fine if you
already run Postgres), Chroma (prototyping), Weaviate (heavier).

**In this project:** [`rag/store.py`](../src/assistant/rag/store.py) wraps
one Qdrant collection; each stored point = one document chunk with its
text, source path, and heading as payload.

## Dense vs sparse — two kinds of "similar"

- **Dense vectors** (embeddings above): capture *meaning*. Great for
  paraphrases; can be fuzzy about exact identifiers.
- **Sparse vectors**: classic keyword matching dressed as a vector. Each
  dimension corresponds to a *specific token*; a text has non-zero values
  only for tokens it actually contains. Exact matches on rare terms
  ("ArgoCD", "SEV1") score very strongly — precisely where dense search
  can be weakest.

**In this project:** [`rag/sparse.py`](../src/assistant/rag/sparse.py) —
each token maps to a stable 32-bit index (hash of the token), value =
1 + log(term frequency). Every chunk is stored with **both** a dense and a
sparse vector (Qdrant "named vectors").

## Hybrid search + RRF

At query time we search **both** spaces and fuse the two ranked lists with
**Reciprocal Rank Fusion**: each candidate scores Σ 1/(k + rank) across the
lists (k≈60). RRF only uses *ranks*, so it needs no score calibration
between the two very different similarity scales — simple and robust.

Then a **reranker** re-orders the top ~20 candidates with a more careful
(but slower) relevance judgment before returning the final top-5. Ours is a
deterministic lexical one ([`rag/rerank.py`](../src/assistant/rag/rerank.py));
API rerankers (voyage, Cohere) implement the same protocol.

Measured effect on our golden set (chapter 03 explains the metrics):
recall@1 went **0.56 → 0.67 (hybrid) → 0.83 (+rerank)**; recall@5 hit
**1.00**. Zero cost, no API keys.

## Our `hash-512` dense embedder — an honest disclosure to make proactively

The offline default dense "embedder"
([`rag/embeddings.py`](../src/assistant/rag/embeddings.py)) is **not a
neural model**. It's *feature hashing*: hash each token into one of 512
slots (with a ± sign to reduce collision bias), count, L2-normalize. Two
texts are "similar" if they share words — lexical overlap, not semantics.

Why it's the default anyway: deterministic, offline, $0, and good enough to
exercise and *measure* the entire pipeline (the 0.83/1.00 numbers above are
real). The semantic upgrade is a config switch: `text-embedding-3-small`
(OpenAI) or `voyage-3` — and `evals/compare_embeddings.py` will print the
before/after table the day keys exist. Saying this out loud *before* anyone
asks is a credibility win, not a weakness.

## One rule that trips everyone up

**Vectors from different embedding models are never comparable.** Each model
defines its own space. Switching models means re-embedding the whole corpus
into a **separate collection** — which is exactly how our comparison harness
is built (one `:memory:` collection per model).

## Questions you might get

**"Why not just full-text search (Elasticsearch)?"** — Our sparse vectors
*are* that family of signal, and we keep them — fused with dense. Pure
keyword search misses paraphrases ("linter" vs "lint" cost us a golden-set
question until hybrid+rerank fixed the ranking); pure dense search fumbles
exact identifiers. Hybrid takes both.

**"512 dimensions? Real models use 1536+."** — Correct; ours is a lexical
stand-in, not a semantic model (see disclosure above). The store doesn't
care about dimensionality — it's a per-collection parameter.

**"How does it scale?"** — Qdrant's HNSW handles millions of points;
ingest is idempotent (deterministic chunk IDs) and incremental; collections
shard/replicate in Qdrant's distributed mode. Our corpus is deliberately
small — the pipeline is what's production-shaped.
