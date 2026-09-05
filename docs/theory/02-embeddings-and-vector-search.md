# 02 — Embeddings & vector search

**What this chapter answers: how text becomes a comparable vector, why this
project keeps both a dense and a sparse index, and how the two are fused at
query time.** It does not cover chunking or the retrieval loop that calls
this machinery for a live question — that is [03-rag.md](03-rag.md).

## 1. The problem: computers can't compare meaning

"How do we ship to prod?" and "What's our deployment process?" share almost
no words, yet mean the same thing. Keyword search (SQL `LIKE`, grep) sees
zero overlap. We need a way to compare **meaning**, not spelling.

## 2. Embeddings: text → a point in space

An **embedding model** turns a piece of text into a vector — a list of
numbers, e.g. 1536 floats. The model is trained so that **texts with similar
meaning land close together** in that space. "ship to prod" and "deployment
process" end up as nearby points; "lunch menu" ends up far away.

Similarity between two vectors is measured with **cosine similarity** — the
angle between them: 1.0 = same direction (same meaning), 0 = unrelated.
"Find relevant docs" becomes geometry: *embed the question, find the nearest
stored vectors.*

## 3. Vector databases, and why Qdrant

Comparing a query against millions of vectors needs a purpose-built store
with approximate-nearest-neighbor indexes (HNSW is the standard algorithm)
— that's a **vector database**. We chose **Qdrant**: single container,
first-class async Python client, payload filters (each vector carries JSON
metadata — we filter by `source` file), named vectors (below), and native
hybrid-search support.

Three other stores were priced against it, and each lost for a stated reason
— not "we didn't consider them" (from [tech-stack.md](../project/tech-stack.md)):

| Alternative | Why it lost |
|---|---|
| **pgvector** | Fine if the project already ran Postgres for something else — it doesn't, so it would be a new dependency for no second benefit |
| **Chroma** | Prototyping-grade; not the target for a project that also wants payload filters and native hybrid search |
| **Weaviate** | Heavier to operate, with no advantage here over a single Qdrant container |
| **LanceDB** | A genuinely nice embedded option, but a smaller ecosystem than Qdrant's for the hybrid + payload-filter combination this project leans on |

**In this project:** [`rag/store.py`](../../src/assistant/rag/store.py) wraps
one Qdrant collection; each stored point = one document chunk with its
text, source path, and heading as payload.

## 4. Dense vs sparse — two kinds of "similar"

- **Dense vectors** (embeddings above): capture *meaning*. Great for
  paraphrases; can be fuzzy about exact identifiers.
- **Sparse vectors**: classic keyword matching dressed as a vector. Each
  dimension corresponds to a *specific token*; a text has non-zero values
  only for tokens it actually contains. Exact matches on rare terms
  ("ArgoCD", "SEV1") score very strongly — precisely where dense search
  can be weakest.

**In this project:** [`rag/sparse.py`](../../src/assistant/rag/sparse.py) —
each token maps to a stable 32-bit index (hash of the token), value =
1 + log(term frequency). Every chunk is stored with **both** a dense and a
sparse vector (Qdrant "named vectors").

## 5. Hybrid search + RRF

At query time we search **both** spaces and fuse the two ranked lists with
**Reciprocal Rank Fusion**: each candidate scores Σ 1/(k + rank) across the
lists (k≈60). RRF only uses *ranks*, so it needs no score calibration
between the two very different similarity scales — simple and robust.

Then a **reranker** re-orders the top ~20 candidates with a more careful
(but slower) relevance judgment before returning the final top-4. Ours is a
deterministic lexical one ([`rag/rerank.py`](../../src/assistant/rag/rerank.py));
API rerankers (voyage, Cohere) implement the same protocol.

Measured effect on our golden set (chapter 03 explains the metrics), 18
questions, hash-512 embedder, `uv run python evals/run_retrieval.py --memory`,
2026-09-04: recall@1 went **0.78 (dense) → 0.83 (hybrid + rerank)**, and the
rerank is the stage that pays: +0.11 over hybrid alone. recall@5 hit
**1.00**. Zero cost, no API keys.

## 6. Our `hash-512` dense embedder — an honest disclosure to make proactively

The offline default dense "embedder"
([`rag/embeddings.py`](../../src/assistant/rag/embeddings.py)) is **not a
neural model**. It's *feature hashing*: hash each token into one of 512
slots (with a ± sign to reduce collision bias), count, L2-normalize. Two
texts are "similar" if they share words — lexical overlap, not semantics.

Why it's the default anyway: deterministic, offline, $0, and good enough to
exercise and *measure* the entire pipeline (the 0.83/1.00 numbers above are
real). The semantic upgrade is a config switch — the same file implements
all three embedders behind one `Embedder` protocol:

| Embedder | Config value | Dimension | Cost | Role here |
|---|---|---:|---|---|
| `HashEmbedder` | `hash` | 512 | $0 | dev/test default — no network, fully deterministic |
| `OpenAIEmbedder` | `openai` (`text-embedding-3-small`) | 1536 (3072 for `-large`) | ~$0.02 / 1M tokens | the "real" profile — one `.env` switch away |
| `VoyageEmbedder` | `voyage` (`voyage-3`) | 1024 (512 for `-lite`) | free comparison allowance | the measured challenger, not the default |

`evals/compare_embeddings.py` will print the before/after table the day
API keys exist. The hosted default wasn't the only semantic option, either:
[tech-stack.md](../project/tech-stack.md) names **BGE-M3** (local,
sentence-transformers, dense+sparse in one model) and **jina** as
alternatives, both passed over for the same reason — a cheap hosted model
gives a fast, unambiguous comparison table for the workshop today; BGE-M3 is
kept in reserve specifically for the day this platform must run fully
on-prem, which hasn't arrived. Saying this out loud *before* anyone asks is a
credibility win, not a weakness.

## 7. One rule that trips everyone up

**Vectors from different embedding models are never comparable.** Each model
defines its own space. Switching models means re-embedding the whole corpus
into a **separate collection** — which is exactly how our comparison harness
is built (one `:memory:` collection per model).

## 8. Questions you might get

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

## 9. Reading it honestly

- **hash-512 is lexical, not semantic — including in the failure mode this
  chapter opens with.** "Ship to prod" vs "deployment process" share no
  words, so the hash embedder alone would score them no better than random;
  it is only the sparse channel's identifier matches plus hybrid fusion that
  make the measured numbers look as good as they do on real paraphrase-heavy
  questions.
- **RRF's `k≈60` and the lexical reranker's scoring are hand-picked, not
  tuned.** There is no separate validation set from the 18-question golden
  set, so a parameter change and a genuine quality change are hard to tell
  apart at this sample size.
- **32-bit sparse hash collisions are "negligible," not measured.** The code
  comment says the index space makes them unlikely; nothing in this project
  independently counts how often two different identifiers collide on this
  corpus.
- **18 questions over a 5-document fixture corpus is a regression harness,
  not a claim about a large, messy real knowledge base.** The numbers prove
  the pipeline works and catch regressions — they do not generalize to
  scale on their own.
- **A dimension change is a new collection, not a migration.** Switching
  `embedding_provider` means re-ingesting into a fresh collection today;
  there is no in-place upgrade path.

## 10. Related

- [01-llm-basics.md](01-llm-basics.md) — tokens and the context window, the budget retrieval exists to respect
- [03-rag.md](03-rag.md) — how chunking and retrieval put these vectors to work end to end
- [../handbook/05-rag-qdrant.md](../handbook/05-rag-qdrant.md) — the same pipeline as this project actually runs it, with the measured table
- [../reference/metrics.md](../reference/metrics.md) — recall@k and MRR defined precisely, including what each hides
- [../project/tech-stack.md](../project/tech-stack.md) — the embedding and vector-DB decisions, alternatives priced and all
