# Embedding comparison — golden retrieval set

Corpus: `evals/corpus/` · questions: 18 · mode: hybrid (dense + sparse RRF) + lexical rerank

| model | recall@1 | recall@5 | MRR |
|---|---:|---:|---:|
| hash-512 | 0.83 | 1.00 | 0.92 |
| text-embedding-3-small | 0.94 | 1.00 | 0.97 |

Not run (no API key configured): voyage — set `ASSISTANT_EMBEDDING_API_KEY` (openai) / `ASSISTANT_VOYAGE_API_KEY` (voyage) and re-run.
