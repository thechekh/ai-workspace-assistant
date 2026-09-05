# 03 — RAG (Retrieval-Augmented Generation)

**What this chapter answers: why retrieval beats fine-tuning or stuffing the
prompt, how this project chunks and indexes documents, and how retrieval
quality is actually measured.** It does not cover how the vectors themselves
are built — see [02-embeddings-and-vector-search.md](02-embeddings-and-vector-search.md)
for that; this chapter is retrieval end to end.

## 1. The problem RAG solves

An LLM knows only (a) what was in its training data — public text, frozen at
a cutoff date — and (b) what's in the current context window. It has never
seen *our* architecture docs, and asked anyway, it will often invent a
plausible-sounding answer (hallucination).

Three ways to give a model private knowledge:

| Approach | Idea | Why we didn't / did |
|---|---|---|
| Fine-tuning | Retrain the model on your docs | Expensive, slow to update (docs change daily), bakes knowledge in opaquely, doesn't cite sources |
| Stuff everything into the prompt | Paste all docs into every request | Blows the context window, costs per token *every question*, drowns the signal |
| **RAG** ✅ | Store docs searchably; retrieve only the relevant few chunks per question and put *those* in the prompt | Cheap, updatable by re-ingesting, and answers cite their sources |

RAG = **R**etrieval-**A**ugmented **G**eneration: retrieve first, then
generate an answer grounded in what was retrieved.

## 2. Ingestion — done once (and re-run when docs change)

**In this project** the knowledge base starts **empty**. Documents arrive
three ways, all landing in the same pipeline: the Documents panel in the UI,
`POST /api/documents`, or the CLI
[`rag/ingest.py`](../../src/assistant/rag/ingest.py)
(`uv run python -m assistant.rag.ingest <folder> --recreate`). The examples
below use `evals/corpus/` because that is the fixture the retrieval eval
measures against — 5 sample internal docs: deployment, service catalog,
standards, incident response, onboarding.

1. **Load** the documents (files from disk, or uploaded text).
2. **Chunk** ([`rag/chunking.py`](../../src/assistant/rag/chunking.py)) —
   split along headings; pack paragraphs to ~1800 chars (~450 tokens).
   Details that matter:
   - Each chunk is prefixed with its **heading breadcrumb**
     ("Service Catalog > billing-service") — cheap context that measurably
     helps retrieval and lets answers cite a location, not just a file.
   - Fenced code blocks are never split (a blank line inside ``` is not a
     paragraph break).
   - Chunk IDs are **deterministic** (uuid5 of source+heading+index), so
     re-ingesting *overwrites* instead of duplicating — idempotent by
     construction.
3. **Embed** each chunk: dense vector + sparse lexical vector (chapter 02).
4. **Upsert** into Qdrant with payload `{text, source, heading, index}`.
   Re-ingesting a source is a **replace**, not a merge:
   [`ingest_chunks`](../../src/assistant/rag/ingest.py) deletes every chunk
   that source already had before upserting the new ones, precisely because
   the deterministic id alone isn't enough — shortening a document or
   renaming a heading changes the id, and the *old* chunk would otherwise
   sit there forever, still searchable, still cited.

Why ~450-token chunks? Too small → a chunk lacks context to be useful; too
big → retrieval gets blurry (one vector averaging many topics) and you waste
prompt budget. Heading-bounded ~300–500 tokens is the well-tested middle.

Markdown, plain text and reStructuredText cover every source this project
has actually needed to ingest. A heavier ingestion path was on the original
plan — [tech-stack.md](../project/tech-stack.md) specced `docling` to parse
PDF, HTML and DOCX before chunking — and it was never built:
[implementation-plan.md](../project/implementation-plan.md)'s roadmap notes
that Markdown/text/RST covered every real source, so the extra parser stayed
unbuilt rather than added on spec.

## 3. Query time — every question

**In this project:** [`rag/retriever.py`](../../src/assistant/rag/retriever.py),
exposed to the agent as the `search_docs` tool
([`agent/tools/search_docs.py`](../../src/assistant/agent/tools/search_docs.py)).

1. The agent decides the question needs docs and calls
   `search_docs(query=...)` (chapter 04 — the *model* makes this decision).
2. Embed the query (dense + sparse).
3. Qdrant hybrid search, RRF fusion → top-20 candidates.
4. Lexical reranker reorders → top-4 survive.
5. Chunks are formatted as
   `[architecture/deployment.md — CI/CD pipeline] (score 0.21) <text>`
   and returned as the tool result — visible to the user in the UI tool card.
6. The model writes its answer **from those chunks**, citing sources.

Grounding is the anti-hallucination mechanism: the model is instructed to
answer from the retrieved text and to say so when the docs don't cover the
question — and the user can always inspect the evidence in the tool card.

## 4. How we know it works: evaluation

Vibes don't survive a Q&A session; numbers do.

- **Golden set** ([`evals/golden.yaml`](../../evals/golden.yaml)): 18 real
  engineer questions, each annotated with the file (and text) that contains
  the answer.
- **recall@k** — fraction of questions whose correct chunk appears in the
  top k results. **MRR** (mean reciprocal rank) — average of 1/rank of the
  first correct result (1.0 = always ranked first).
- Runner: `uv run python evals/run_retrieval.py --memory` (self-contained:
  in-process Qdrant, no Docker).
- Every metric in full — what each hides, and how groundedness differs from
  both — is in [reference/metrics.md](../reference/metrics.md).

Measured, free offline embedder, 18 questions, recorded 2026-09-04
(`uv run python evals/run_retrieval.py --memory`):

| config | recall@1 | recall@5 | MRR |
|---|---:|---:|---:|
| dense only, no rerank | 0.78 | 0.94 | 0.86 |
| hybrid, no rerank | 0.72 | 1.00 | 0.86 |
| dense + rerank | 0.89 | 1.00 | 0.94 |
| hybrid + rerank (default) | **0.83** | **1.00** | **0.92** |

The instructive question is the golden set's own
*"What linter and formatter do we use for Python?"*
([`evals/golden.yaml`](../../evals/golden.yaml)): the corpus answer says
*"Lint and format with ruff"*
([`evals/corpus/guidelines/coding-standards.md`](../../evals/corpus/guidelines/coding-standards.md))
— a verb, not the noun the question asks with. Different tokens, a pure
lexical gap. It is the one question where the sparse channel visibly helps
(rank 3 dense, rank 2 hybrid). That is the case a semantic embedding model
closes completely — and `evals/compare_embeddings.py` is standing by
to measure exactly that when API keys exist.

## 5. Questions you might get

**"Why RAG instead of fine-tuning?"** — Docs change weekly; re-ingesting is
minutes and $0, retraining is neither. RAG also cites sources (auditable)
and keeps knowledge out of the opaque weights. Fine-tuning is for *style and
behavior*, not for facts that change.

**"What if the answer spans multiple documents?"** — Retrieval returns
top-k across the whole corpus, so chunks from different files land in the
same prompt; the model synthesizes. For deeper multi-hop questions the agent
can call `search_docs` multiple times with refined queries — the loop allows
up to 6 steps.

**"How do you keep the index fresh?"** — Idempotent ingest: re-uploading a
source replaces its chunks in place, because chunk ids are derived from
(source, index). There is no scheduled re-index — a document is embedded once,
at upload, so nothing drifts out of date on its own. Deterministic
IDs mean changed chunks overwrite in place.

**"18 questions — isn't that small?"** — It's a regression harness, not a
benchmark: enough to catch retrieval regressions per change and to compare
configurations on equal footing. Growing it is a content task, not an
engineering one.

## 6. Reading it honestly

- **Deleting a file from the corpus folder doesn't delete it from the
  index.** Re-ingestion only replaces sources it *sees* in the current run
  ([`rag/ingest.py`](../../src/assistant/rag/ingest.py)); a file removed from
  the folder is simply never visited again, so its chunks stay searchable
  and citable until someone runs `--recreate` for a full rebuild. This is a
  real, acknowledged gap, not a hypothetical one.
- **recall@k and MRR say nothing about whether the model told the truth.**
  A system can retrieve the perfect chunk and still write something it never
  said — that failure mode is invisible to every number in this chapter and
  is exactly what [reference/ragas.md](../reference/ragas.md)'s groundedness
  metric exists to catch instead.
- **18 questions over a 5-document fixture corpus is a regression harness,
  not a claim about production-scale retrieval.** It proves the pipeline
  works end to end and catches regressions per change; it does not claim to
  generalize to a large, messy real knowledge base.
- **Heading-aware chunking silently degrades on badly-structured source
  documents.** A file with no headings, or misleading ones, chunks worse and
  nothing today flags that at ingest time.
- **The reranker is only evaluated against this one golden set.** There is
  no separate, held-out set of real production queries to confirm the
  ablation table generalizes beyond the 18 questions it was measured on.

## 7. Related

- [02-embeddings-and-vector-search.md](02-embeddings-and-vector-search.md) — how the dense and sparse vectors this chapter searches are actually built
- [04-tool-calling-and-agents.md](04-tool-calling-and-agents.md) — how the model decides to call `search_docs` in the first place
- [../handbook/05-rag-qdrant.md](../handbook/05-rag-qdrant.md) — the same pipeline as this project runs it, ingest and query both
- [../reference/metrics.md](../reference/metrics.md) — recall@k, MRR and groundedness defined precisely, with what each hides
- [../reference/ragas.md](../reference/ragas.md) — the judge that checks what recall@k and MRR cannot: whether the answer was honest
