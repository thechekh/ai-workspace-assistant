# Evaluation metrics

**Every number this project reports about its own quality — what each one
means, how it is computed, where it comes from, how to reproduce it, and
what it cannot tell you — with the retrieval table re-measured and every
row reproduced exactly.** The LLM judge behind the generation metric has
its own page, [ragas.md](ragas.md); the retrieval pipeline the numbers
grade is [handbook/05](../handbook/05-rag-qdrant.md). Re-measured
2026-09-05.

## 1. What the metrics are

Two families, and the difference matters more than any single score:

| | Retrieval metrics | Generation metrics |
|---|---|---|
| Answer the question | did we **find** the right text? | did the model **use** it honestly? |
| Metrics | recall@k, MRR | faithfulness (groundedness) |
| Computed by | arithmetic over ranks | another LLM reading the answer |
| Deterministic | yes — same input, same number | no — a judge's opinion |
| Cost | free, offline, ~2 s | one key and several LLM calls per question |
| Where it runs | `pytest` and **CI, as a gate** | on demand, **never in CI** |
| Command | `evals/run_retrieval.py --memory` | `evals/run_ragas.py` |

A system can score perfectly on the first family and still lie to users,
which is precisely why both exist. Precision and accuracy are deliberately
not measured: retrieval feeds one answer through a fixed top-k window, so
what matters is whether the right chunk is near the top, which recall@k and
MRR capture directly. At a fixed k, precision is a rescaling of recall, not
new information.

## 2. How they are computed

### recall@k — "how often is the right chunk in the top k?"

```
recall@k = (questions whose correct chunk appears in the top k) / (all questions)
```

A hit anywhere in the window counts the same; position is ignored. Two
values are reported because they answer different questions: **recall@1** is
how often the *first* result is already right, the number closest to answer
quality because the top chunk dominates what the model reads; **recall@5**
is how often the answer is in the window at all — at **1.00**, retrieval
never entirely loses it, and every remaining problem is one of *ordering*,
which a reranker can fix.

*Worked example, from the measured run.* Of the 18 golden questions, 15 had
the right chunk at rank 1 and the other 3 at rank 2, so recall@1 = 15/18 =
**0.83** and recall@5 = 18/18 = **1.00**. One of the three is *"What
accounts do I need in my first week?"*, ranked second in the capture in §5.

What it hides: rank within the window. A system that puts the answer at
position 5 every time scores the same as one that puts it at position 2.

### MRR — "how high up is the right chunk, on average?"

```
MRR = mean over questions of 1 / (rank of the first correct chunk)
```

| Rank of the correct chunk | 1 | 2 | 3 | 4 | 5 | not found |
|---|---|---|---|---|---|---|
| Contribution | 1.00 | 0.50 | 0.33 | 0.25 | 0.20 | 0 |

The reciprocal is what makes it useful: the gap between rank 1 and rank 2
is large, the gap between rank 4 and rank 5 small, matching how much those
positions matter when the model reads only the top few. *Worked example:*
15 questions at rank 1 and 3 at rank 2 give (15 × 1.00 + 3 × 0.50) / 18 =
**0.92**, the measured MRR. Read against recall: MRR sits between recall@1
and recall@5 by construction; close to recall@1 means hits are usually first,
much lower means answers are found but buried, and reranking is where the
improvement is. It hides multi-chunk answers: only the *first* correct chunk
counts.

### Faithfulness — "is every claim supported by the retrieved text?"

Ragas' name for groundedness, the measurable opposite of hallucination,
which retrieval metrics cannot see at all. Two LLM passes: split the answer
into atomic claims, then verify each against the retrieved chunks.

```
faithfulness = (claims supported by the context) / (all claims in the answer)
```

*Worked example, from the fixture corpus.* The retrieved chunk says
*"billing-service generates PDF invoices on a nightly schedule."* The
answer *"billing-service generates invoices every 5 minutes and is written
in Rust"* decomposes into three claims of which one is supported →
**0.33** — the hand check that preceded the automated control. A low score
means "unsupported", which is not always "false": a true fact absent from
the documents still counts against the answer, and that is the intended
strictness, because this assistant's contract is to answer *from the
knowledge base*. Declined answers are dropped before judging — "I could not
find that" is perfectly faithful, and counting it would flatter the mean.

## 3. Where it lives in this project

| File | Role |
|---|---|
| [evals/golden.yaml](../../evals/golden.yaml) | the 18 hand-written questions, each with `expect_source` and optionally `expect_text` |
| [evals/corpus/](../../evals/corpus/) | the fixture documents the golden set points into — a test fixture, never loaded by the running app |
| [evals/run_retrieval.py](../../evals/run_retrieval.py) | recall@k and MRR: in-memory Qdrant, `--mode`, `--no-rerank`, `--check`, `--record`, `--trend` |
| [evals/baseline.json](../../evals/baseline.json) | the floor CI enforces (`metrics`, tolerance 0.005) and the judged rules (`judged`: floor 0.80, control gap 0.20) |
| [evals/history.jsonl](../../evals/history.jsonl) | every recorded run, retrieval and judged, with its commit |
| [evals/run_ragas.py](../../evals/run_ragas.py) | faithfulness with the negative control — [ragas.md](ragas.md) |
| [tests/test_eval_gate.py](../../tests/test_eval_gate.py) | the pipeline really achieves the committed baseline; a drop is reported and float noise is not |
| [.github/workflows/ci.yml](../../.github/workflows/ci.yml) | runs `run_retrieval.py --memory --check` on every push |

What one retrieval run does, in order: ingest the corpus into an in-memory
Qdrant with the configured embedder → for each golden question, search with
the configured mode and reranker → find the rank of the first chunk from
`expect_source` (containing `expect_text` if given) → recall@1, recall@5,
MRR over the 18 → optionally compare against `baseline.json` and exit 1, or
append to the history.

## 4. How to run it

```sh
# the retrieval eval (about 2 s, offline). Force the hash embedder: a .env that
# points embeddings at OpenAI would silently change the numbers — and cost cents.
ASSISTANT_EMBEDDING_PROVIDER=hash uv run python evals/run_retrieval.py --memory

# the ablations behind the table in §6
ASSISTANT_EMBEDDING_PROVIDER=hash uv run python evals/run_retrieval.py --memory --mode dense --no-rerank
ASSISTANT_EMBEDDING_PROVIDER=hash uv run python evals/run_retrieval.py --memory --no-rerank
ASSISTANT_EMBEDDING_PROVIDER=hash uv run python evals/run_retrieval.py --memory --mode dense

# what CI runs, and the trend
ASSISTANT_EMBEDDING_PROVIDER=hash uv run python evals/run_retrieval.py --memory --check
uv run python evals/run_retrieval.py --trend

# the judged metric — its own page explains the side environment and the flags
UV_PROJECT_ENVIRONMENT=.venv-evals uv run python -m evals.run_ragas --check --control --record
```

PowerShell: `$env:ASSISTANT_EMBEDDING_PROVIDER = "hash"` once per shell,
then the same `uv run` commands.

| Run | Wall clock | Cost |
|---|---|---|
| retrieval eval, one configuration | ~2 s | nothing |
| all four configurations | ~10 s | nothing |
| the judged metric, 18 questions with the control | ~6 min | a few cents on `gpt-4.1-nano` |

## 5. How to see it

![The retrieval eval's real output: 18 questions with the rank of the correct chunk, the summary line, and the three ablations' summary lines](../images/metrics-retrieval-run.png)

Line by line:

- **`[rank 1] What happens during a SEV1 incident?`** — one line per golden
  question, with the rank at which the expected chunk appeared. Fifteen are
  `[rank 1]`.
- **`[rank 2] What accounts do I need in my first week?`** — one of the
  three questions whose answer came second. Each of these costs recall@1
  one eighteenth and MRR half of that.
- **`embedder: hash-512   mode: hybrid   rerank: True`** — the configuration
  being scored; check it before trusting a number, because `.env` can change
  it.
- **`recall@1: 0.83   recall@5: 1.00   mrr: 0.92`** — the default row of the
  table, reproduced on 2026-09-05 to the digit.
- **The three ablation lines** — the same command with `--mode dense` and
  `--no-rerank`, each reproducing its row of the table.

The judged metric's output is read line by line in [ragas.md §5](ragas.md).

## 6. Proving it

**The table, re-measured.** hash-512 embedder, 18 questions, every row
reproduced by the commands in §4 on 2026-09-05:

| config | recall@1 | recall@5 | MRR |
|---|---:|---:|---:|
| dense only, no rerank | 0.78 | 0.94 | 0.86 |
| hybrid (RRF), no rerank | 0.72 | 1.00 | 0.86 |
| dense + rerank | 0.89 | 1.00 | 0.94 |
| **hybrid + rerank** *(default)* | **0.83** | **1.00** | **0.92** |

Read it honestly, because a reviewer will. The reranker is the stage that
pays: hybrid goes from 0.72 to 0.83 recall@1, **+0.11**, the only stage that
moves MRR. Hybrid alone *hurts* recall@1 (0.78 → 0.72) while fixing recall@5
(0.94 → 1.00): it drags the right chunk into the window on the question
dense search missed entirely, and the reranker then lifts it. And the
default is not the best row — dense + rerank scores 0.89. Hybrid + rerank is
the default because recall@5 of 1.00 means the evidence is *always* in the
window, and lexical matching is what saves identifier-style queries that a
512-dimension hash embedder handles badly; on the real embedder the
difference shrinks.

**The gate.** [baseline.json](../../evals/baseline.json) holds the default
row and CI fails the build if any metric drops more than 0.005 below it.
That catches the failure no unit test can: a chunking tweak or a fusion
change that leaves every test green and quietly makes answers worse. A
regression prints:

```
RETRIEVAL REGRESSION vs evals/baseline.json:
  recall@1: 0.722 < 0.833 baseline (down 0.111)
```

Lowering a number in `baseline.json` is a deliberate act: do it in the same
commit as the change that caused it, and say why in the message.

**The judged number, with its control.** Recorded 2026-09-04 over all 18
questions, `gpt-4.1-nano` answering and judging: faithfulness **1.00** on
the clean answers and **0.48** on the same answers with three fabricated
claims appended, against a floor of 0.80 and a required drop of 0.20. The
second number is what makes the first credible — a judge that says yes to
everything also scores 1.00 — and [ragas.md](ragas.md) shows the run.

## 7. Showing it live

Two seconds, no key:

1. Run the first command in §4 — *"eighteen hand-written questions against
   the fixture corpus, in-memory, offline; the rank of the right chunk for
   each."*
2. Point at the summary line — *"0.83 first-try, 1.00 in the window, and CI
   fails the build if either drops."*
3. Run it again with `--no-rerank` — *"same questions, reranker off: 0.72.
   That is the stage earning its place, measured rather than argued."*

## 8. Reading it honestly

- **n = 18.** One question is ~0.06 of recall@1, so a single flip moves the
  headline by six points. The lowest-ranked questions matter more than the
  second decimal.
- **The questions are hand-written on purpose** — questions generated from
  the documents reuse their vocabulary and make retrieval look better than
  real users will — but eighteen hand-written questions are still one
  author's idea of what gets asked.
- **hash-512 is the dev embedder.** The table is the offline configuration
  CI can run; the production profile uses `text-embedding-3-small`, and the
  ranking of configurations can differ there.
- **Single-label scoring.** Each question has one expected source, so a
  question whose answer is spread across chunks scores on the first one
  found.
- **The judged number is one model judging itself**, on one day, with the
  caveats [ragas.md §8](ragas.md) lists: self-preference, variance,
  unsupported ≠ false.

Which number to quote when:

| Question you are asked | Metric | Where |
|---|---|---|
| "Does your search work?" | recall@1 / recall@5 | `run_retrieval.py --memory` |
| "How good is the ranking?" | MRR | same command |
| "Did the reranker earn its place?" | the ablation table in §6 | `--no-rerank` |
| "How do you know it doesn't hallucinate?" | faithfulness with the control | `run_ragas.py --control` |
| "Has quality regressed?" | the CI gate | `run_retrieval.py --memory --check` |
| "What changed over time?" | the trend log | `run_retrieval.py --trend` |

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| the numbers differ from the table and the summary line says `embedder: openai-…` | `.env` sets `ASSISTANT_EMBEDDING_PROVIDER=openai`, so the eval used real embeddings (and cost cents) | prefix the command with `ASSISTANT_EMBEDDING_PROVIDER=hash` |
| `RETRIEVAL REGRESSION vs evals/baseline.json: recall@1: … < … baseline` | a change to chunking, fusion, reranking or the tokenizer moved a question's rank | inspect the `[rank N]` lines to find which question; fix, or lower the baseline deliberately in the same commit |
| `no history yet — run with --record to start one` | `--trend` before any recorded run | `--record` once |
| `ragas judges with a real model — set ASSISTANT_LLM_PROVIDER and a key` | the judged metric refused the fake provider | point `.env` at a real provider; the retrieval eval is the free check |
| all questions `[rank 1]` after a change that should not have helped | the eval is scoring against a stale in-memory index or a changed golden set | check `git diff evals/`; the corpus and golden set are fixtures |

## 10. Related

- [ragas.md](ragas.md) — the judge: what it is, the recorded run, the negative control, how to demo it
- [handbook/05 — RAG & Qdrant](../handbook/05-rag-qdrant.md) — the pipeline these numbers grade, stage by stage
- [theory/02 — Embeddings & vector search](../theory/02-embeddings-and-vector-search.md) — why a hash embedder scores differently from a semantic one
- [evals/run_retrieval.py](../../evals/run_retrieval.py) — the scorer; every flag in §4 is a line in it
- [handbook/09 — Testing & operations](../handbook/09-testing-operations.md) — where the CI gate sits among the other gates
