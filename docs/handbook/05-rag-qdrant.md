# 05 — RAG & Qdrant: how the assistant answers from *our* docs

Concept primer: [theory/03-rag.md](../theory/03-rag.md). This chapter is the
project-specific pipeline, end to end.

## Where documents come from

The knowledge base **starts empty**. Documents are added at runtime:

| How | When to use |
|---|---|
| **Documents panel** in the UI header — drop files or paste text | day to day |
| `POST /api/documents` (multipart `files=` and/or `text=`+`source=`); `GET` to list, `DELETE /api/documents/{source}` to remove | scripting, CI |
| `uv run python -m assistant.rag.ingest <folder>` | bulk import |
| `ASSISTANT_CORPUS_DIR=<folder>` | keep a folder synced — **required** for the nightly job and the Re-index button; without it `POST /api/reindex` returns 400 |

Re-uploading a source **replaces** it rather than duplicating, because chunk
ids are derived from `(source, index)`.

`search_docs` distinguishes *nothing indexed yet* from *nothing relevant* —
they need different answers, and only the first is the user's to fix.

## The eval fixture

[evals/corpus/](../../evals/corpus/) — Markdown files in three areas
(`architecture/`, `guidelines/`, `onboarding/`), ~30 chunks after splitting.
This is the "internal engineering documentation" the system prompt promises.
Only `*.md` files under the corpus directory are ingested; the chunk's
`source` is its relative path (that's what citations show).

## Ingestion pipeline (`python -m assistant.rag.ingest`)

```
*.md file ──> chunk_markdown() ──> per chunk:
                                     dense vector   (embedder, 512-dim)
                                     sparse vector  (lexical, md5-token index)
                                   ──> Qdrant upsert (deterministic ids)
```

1. **Chunking** ([rag/chunking.py](../../src/assistant/rag/chunking.py)) —
   heading-aware Markdown splitting; each chunk keeps its `source` file and
   `heading` path (both shown in citations). Code fences stay intact.
   Deterministic chunk ids → re-running ingest overwrites in place
   (idempotent); `--recreate` drops the collection first (use it to *remove*
   stale sources).
2. **Dense embedding** ([rag/embeddings.py](../../src/assistant/rag/embeddings.py)) —
   provider-switched:
   - `hash` *(default)*: offline feature-hashing into 512 dims — zero cost,
     deterministic, good enough for lexical-ish matching; the reason the
     whole platform runs without keys.
   - `openai` (`text-embedding-3-small`) or `voyage` (voyage-3): real
     semantic embeddings; set the key and **re-ingest**.
3. **Sparse encoding** ([rag/sparse.py](../../src/assistant/rag/sparse.py)) —
   classic keyword signal: each token maps to a stable 32-bit index
   (md5-based) with sublinear term-frequency values.
4. **Storage** ([rag/store.py](../../src/assistant/rag/store.py)) — one Qdrant
   collection (`docs`) with **named vectors** `dense` (cosine) + `sparse`;
   payload carries text/source/heading.

Three ways to (re)ingest:

| How | When |
|---|---|
| `uv run python -m assistant.rag.ingest <folder> [--recreate]` | CLI, always works |
| UI **Re-index** button / `POST /api/reindex` | queued via taskiq (real Redis + worker running) |
| same, in `fakeredis://` mode | runs inline in the request |

## Query pipeline (what `search_docs` actually does)

```
query ──> embed (dense) ──> Qdrant hybrid search ──> RRF fusion (server-side)
      └─> encode (sparse) ─┘        top-20 candidates
                                        │
                              LexicalReranker (token overlap)
                                        │  top-4
                              relevance gate (query_overlap > 0)
                                        │
                       "[source — heading] (score 0.87)\n<chunk>" blocks
```

- **Hybrid + RRF** ([rag/retriever.py](../../src/assistant/rag/retriever.py)):
  dense and sparse searches run as one Qdrant query; Reciprocal Rank Fusion
  merges the two rankings. `ASSISTANT_RETRIEVAL_MODE=dense` disables sparse.
- **Rerank** ([rag/rerank.py](../../src/assistant/rag/rerank.py)): deterministic
  lexical reranker reorders the top-20 by meaningful-token overlap with the
  query (stopwords ignored), retrieval score as tie-break. Offline stand-in
  for API rerankers (voyage/cohere slot into the same protocol).
- **Relevance gate** (`query_overlap`, applied in the tool): retrieval always
  returns top-k *even for garbage queries*, and RRF/hash scores are not
  calibrated — so chunks sharing **no** meaningful token with the query
  (prefix-tolerant: "deploy" matches "deployment") are dropped. If nothing
  survives, the model receives an explicit *"No relevant documents found …
  do not retry with a rephrased query — tell the user"* message. This is the
  fix for confident-looking irrelevant results (and it kills the
  search-again loop that weaker models fall into).
- Every retrieval emits: span `rag.retrieve` (mode, candidates, results, top
  score), log `rag.retrieved` (with `top_source`), histogram
  `assistant_retrieval_seconds{mode}`.

## Measured quality (golden set)

18 golden questions ([evals/golden.yaml](../../evals/golden.yaml)), hash-512
embedder — each pipeline stage earns its place:

| config | recall@1 | recall@5 | MRR |
|---|---:|---:|---:|
| dense only, no rerank | 0.78 | 0.94 | 0.86 |
| hybrid (RRF), no rerank | 0.72 | 1.00 | 0.86 |
| dense + rerank | 0.89 | 1.00 | 0.94 |
| hybrid + rerank *(default)* | **0.83** | **1.00** | **0.92** |

Read that honestly: **the rerank is what earns its place** (+0.11 recall@1,
and it is the only stage that moves MRR much). Sparse fusion pays for
recall@5 — every question lands in the top 5 — but on recall@1 dense+rerank
is one question ahead of the default. One question out of 18 is noise, and
the two configurations disagree in opposite directions: hybrid wins the
lexical-gap question ("linter/formatter" vs the docs' "lint/format", rank
3 → 2), dense wins "what accounts do I need in my first week" (rank 1 → 2).

Hybrid stays the default because sparse vectors are insurance against
vocabulary mismatch that costs nothing at query time, and because this
ablation cannot really separate the two signals: `hash-512` is a *lexical*
hash, so "dense" here is already keyword-ish. With a real semantic embedder
the two channels diverge and the fusion matters more — which is exactly what
`evals/compare_embeddings.py` is there to measure.

Reproduce: `uv run python evals/run_retrieval.py --memory` (no Docker
needed). Compare embedders: `uv run python -m evals.compare_embeddings` →
writes [evals/results-embeddings.md](../../evals/results-embeddings.md).

### What a real embedder buys — measured

The offline `hash-512` embedder is a *lexical* hash, so it cannot match
wording it has never seen. Swapping in a semantic model, same corpus, same
18 questions, same hybrid+rerank pipeline:

| embedder | recall@1 | recall@5 | MRR | cost |
|---|---:|---:|---:|---|
| `hash-512` (offline default) | 0.83 | 1.00 | 0.92 | $0 |
| `text-embedding-3-small` | **0.94** | 1.00 | **0.97** | fractions of a cent |

Three of the questions the hash embedder ranked second or third move to
first place. That is the vocabulary-mismatch gap closing: the semantic model
matches *meaning*, so a question phrased with different words than the
document still lands. It also reframes the hybrid-vs-dense ablation — with a
lexical stand-in for the dense channel, dense and sparse were measuring
nearly the same thing, which is exactly why that comparison was so close.

Set `ASSISTANT_EMBEDDING_API_KEY` and re-run `compare_embeddings` to
reproduce; the table regenerates itself.

## Watching Qdrant itself

- **Web UI**: http://localhost:6333/dashboard → collection `docs` → browse
  points (text, source, heading payloads), vector config, count.
- **API**: `curl localhost:6333/collections/docs` → status, `points_count`
  (30 for the clean corpus), vector schema.
- **From the app**: `/api/health` pings Qdrant with a count query and
  reports latency + points; the UI health dot turns amber if it's down.
- If Qdrant is down, `search_docs` returns an error *result* (the turn
  survives — `assistant_tool_calls_total{status="crash"}` counts it).

## Gotchas learned in live testing

- **Don't ingest unrelated repos into `docs`** — everything in the collection
  is treated as "our internal docs" by the system prompt. (A test repo's
  Chinese README once ended up in answers this way.) Recover with
  `... ingest <folder> --recreate`.
- **Changing embedder = re-ingest** — vectors from different embedders are
  incompatible; `--recreate` to switch cleanly.
- The displayed `(score 0.87)` is the *retrieval* score (RRF/cosine), kept
  for debugging; relevance decisions use lexical overlap, not that number.
