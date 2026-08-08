# 03 — RAG (Retrieval-Augmented Generation)

## The problem RAG solves

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

## Ingestion — done once (and re-run when docs change)

**In this project:** [`rag/ingest.py`](../../src/assistant/rag/ingest.py); run
`uv run python -m assistant.rag.ingest docs_corpus --recreate`.

1. **Load** every `*.md` under `docs_corpus/` (5 sample internal docs:
   deployment, service catalog, standards, incident response, onboarding).
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

Why ~450-token chunks? Too small → a chunk lacks context to be useful; too
big → retrieval gets blurry (one vector averaging many topics) and you waste
prompt budget. Heading-bounded ~300–500 tokens is the well-tested middle.

## Query time — every question

**In this project:** [`rag/retriever.py`](../../src/assistant/rag/retriever.py),
exposed to the agent as the `search_docs` tool
([`agent/tools.py`](../../src/assistant/agent/tools.py)).

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

## How we know it works: evaluation

Vibes don't survive a Q&A session; numbers do.

- **Golden set** ([`evals/golden.yaml`](../../evals/golden.yaml)): 18 real
  engineer questions, each annotated with the file (and text) that contains
  the answer.
- **recall@k** — fraction of questions whose correct chunk appears in the
  top k results. **MRR** (mean reciprocal rank) — average of 1/rank of the
  first correct result (1.0 = always ranked first).
- Runner: `uv run python evals/run_retrieval.py --memory` (self-contained:
  in-process Qdrant, no Docker).

Measured, free offline embedder, 18 questions:

| config | recall@1 | recall@5 | MRR |
|---|---:|---:|---:|
| dense only | 0.56 | 0.94 | 0.72 |
| hybrid | 0.67 | 1.00 | 0.80 |
| hybrid + rerank (default) | **0.83** | **1.00** | **0.92** |

The story behind the one famous miss: "What linter and formatter do we use?"
failed on dense-only because the docs say "*lint* and *format*" while the
question says "*linter/formatter*" — different tokens, a pure lexical gap.
Hybrid + rerank fixed its ranking. That's the case a semantic embedding
model closes completely — and `evals/compare_embeddings.py` is standing by
to measure exactly that when API keys exist.

## Questions you might get

**"Why RAG instead of fine-tuning?"** — Docs change weekly; re-ingesting is
minutes and $0, retraining is neither. RAG also cites sources (auditable)
and keeps knowledge out of the opaque weights. Fine-tuning is for *style and
behavior*, not for facts that change.

**"What if the answer spans multiple documents?"** — Retrieval returns
top-k across the whole corpus, so chunks from different files land in the
same prompt; the model synthesizes. For deeper multi-hop questions the agent
can call `search_docs` multiple times with refined queries — the loop allows
up to 6 steps.

**"How do you keep the index fresh?"** — Idempotent ingest + the taskiq
nightly re-index job + the UI's Re-index button (chapter 10). Deterministic
IDs mean changed chunks overwrite in place.

**"18 questions — isn't that small?"** — It's a regression harness, not a
benchmark: enough to catch retrieval regressions per change and to compare
configurations on equal footing. Growing it is a content task, not an
engineering one.
