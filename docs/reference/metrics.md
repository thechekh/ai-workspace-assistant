# Evaluation metrics

Every number this project reports about its own quality, what it means, how it
is computed, and what it cannot tell you.

There are two families here and the difference matters more than any single
score:

| | Retrieval metrics | Generation metrics |
|---|---|---|
| Answer the question | did we **find** the right text? | did the model **use** it honestly? |
| Metrics | recall@k, MRR | faithfulness (groundedness) |
| Computed by | arithmetic over ranks | another LLM reading the answer |
| Deterministic | yes — same input, same number | no — a judge's opinion |
| Cost | free, offline, ~2 s | one key and several LLM calls per question |
| Where it runs | `pytest` and **CI, as a gate** | on demand, **never in CI** |
| Command | `evals/run_retrieval.py --memory` | `evals/run_ragas.py` |

A system can score perfectly on the first family and still lie to users. That
is precisely why both exist.

---

## Retrieval metrics

Both are computed in [evals/run_retrieval.py](../../evals/run_retrieval.py)
over the 18-question golden set in
[evals/golden.yaml](../../evals/golden.yaml). Each question records *where* its
answer lives (`expect_source`, and optionally `expect_text`), so scoring is a
matter of finding the rank at which the right chunk appeared — no model, no
judgement, no cost.

### recall@k

**"How often is the right chunk somewhere in the top k results?"**

```
recall@k = (questions whose correct chunk appears in the top k) / (all questions)
```

A hit anywhere in the top k counts the same; position is ignored. Two values
are reported, and they answer different questions:

- **recall@1** — how often the *first* result is already right. This is the
  number that tracks answer quality most closely, because the top chunk
  dominates what the model reads.
- **recall@5** — how often the right chunk is in the window at all. Reaching
  **1.00** means retrieval never entirely loses the answer; every remaining
  problem is one of *ordering*, which a reranker can fix.

*Worked example.* Of 18 questions, if 15 have the right chunk ranked first,
recall@1 = 15/18 = **0.83**. If all 18 have it somewhere in the top five,
recall@5 = 18/18 = **1.00**.

**What it hides.** It is blind to rank within the window, so a system that
puts the answer at position 5 every time scores identically to one that puts
it at position 2. That blind spot is exactly what MRR covers.

### MRR — mean reciprocal rank

**"How high up is the right chunk, on average?"**

```
MRR = mean over questions of 1 / (rank of the first correct chunk)
```

A miss contributes 0. The reciprocal is what makes it useful: the gap between
rank 1 and rank 2 is large (1.00 → 0.50), while the gap between rank 4 and
rank 5 is small (0.25 → 0.20) — which matches how much those positions
actually matter when the model only reads the top few.

| Rank of the correct chunk | 1 | 2 | 3 | 4 | 5 | not found |
|---|---|---|---|---|---|---|
| Contribution | 1.00 | 0.50 | 0.33 | 0.25 | 0.20 | 0 |

**Reading it against recall.** MRR sits between recall@1 and recall@5 by
construction. Close to recall@1 means the hits you get are usually rank 1;
much lower means answers are being found but buried, and reranking is where
the improvement is.

**What it hides.** Only the *first* correct chunk counts. A question whose
answer is spread over three chunks scores the same whether the retriever found
one of them or all three.

### Why not precision, or accuracy?

Deliberately not measured. Retrieval here feeds a single answer through a fixed
top-k window, so what matters is whether the right chunk is near the top —
which recall@k and MRR capture directly. Precision would reward a system that
returns fewer, safer results, and at a fixed k it is a rescaling of recall
rather than new information.

### The measured numbers

hash-512 embedder, 18 questions, reproducible with
`uv run python evals/run_retrieval.py --memory`:

| config | recall@1 | recall@5 | MRR |
|---|---:|---:|---:|
| dense only, no rerank | 0.78 | 0.94 | 0.86 |
| hybrid (RRF), no rerank | 0.72 | 1.00 | 0.86 |
| dense + rerank | 0.89 | 1.00 | 0.94 |
| **hybrid + rerank** *(default)* | **0.83** | **1.00** | **0.92** |

[evals/baseline.json](../../evals/baseline.json) holds the default row, and CI
fails the build if any metric drops below it by more than 0.005. Full
discussion of what the ablation does and does not prove is in
[handbook chapter 05](../handbook/05-rag-qdrant.md).

---

## Generation metrics

### Groundedness (faithfulness)

**"Is every claim in the answer supported by the text the model was given?"**

Groundedness — Ragas calls the metric **faithfulness** — is the direct,
measurable opposite of hallucination. Retrieval metrics cannot see it at all:
a system can retrieve the perfect chunk and then write something the chunk
never said.

It is computed in two LLM passes:

1. **Decompose.** Split the answer into atomic claims.
   *"billing-service generates invoices nightly and is written in Rust"*
   → `["billing-service generates invoices nightly", "billing-service is written in Rust"]`
2. **Verify.** For each claim, ask whether the retrieved context supports it.

```
faithfulness = (claims supported by the context) / (all claims in the answer)
```

In the example, if the context says invoices are generated nightly but never
mentions Rust, one claim of two is supported → **0.5**.

**How to read it.** `1.0` means every statement traces back to a retrieved
chunk. Anything below is the fraction of the answer the model invented.
Because it compares the answer against the *retrieved context* rather than
against ground truth, a low score means "unsupported", which is not always the
same as "false" — a model can state something true that simply was not in the
documents, and that still counts against it. That is the intended strictness:
this system's contract is to answer *from the knowledge base*.

**What it does not measure.** Whether the answer is *useful* or *complete*.
"I could not find that" is perfectly faithful and scores 1.0, which is why
[run_ragas.py](../../evals/run_ragas.py) drops declined answers before judging
— counting an honest refusal as a success would flatter the number.

### Why it is not a CI gate

Three reasons, each sufficient on its own:

1. **It needs a key.** This project's first invariant is that a check
   requiring a key or a container is a check that will not be run. The
   retrieval gate stays green on a laptop with no accounts; a judged metric
   cannot.
2. **It is not deterministic.** The retrieval gate compares against a baseline
   with a 0.005 tolerance. A judge's score moves between runs on identical
   input, so any threshold that tight would flake, and a threshold loose
   enough not to flake would catch nothing.
3. **It costs real money.** Faithfulness spends several LLM calls per
   question; all 18 is roughly 200 calls, and the negative control doubles
   the judging. A few cents on `gpt-4.1-nano` — but cents per push is exactly
   the spend a free offline gate exists to avoid.

So it is a **floor with headroom, checked on demand** — `--check` fails the
run below the `judged` floor in `baseline.json`, `--record` keeps the trend in
`history.jsonl` — but never a CI gate. That split is the point: *retrieval
quality is gated in CI because it is deterministic and free; generation
quality is checked on demand because it is neither.*

### The measured number

The full golden set, `gpt-4.1-nano` both answering and judging, with the
negative control — recorded 2026-09-04 in
[history.jsonl](../../evals/history.jsonl):

```
judge: gpt-4.1-nano   questions: 18
faithfulness: 1.00
faithfulness with fabricated claims: 0.48
lowest-scoring questions:
  0.92  What is our deployment architecture?
judge gate: OK (floor and control gap in evals/baseline.json)
```

Read the two numbers together. **1.00** on its own would be suspicious — a
judge that says yes to everything also scores 1.00. **0.48** on the *same*
answers with three fabricated claims appended is what makes the first number
mean something: the judge found every invented claim unsupported, and a
grounded k-claim answer plus three inventions lands at k/(k+3), which for
answers this short is about a half. The gate in
[baseline.json](../../evals/baseline.json) demands a floor of 0.80 and a
control drop of at least 0.20; this run cleared both with room to spare.

The 0.92 is one claim in the deployment-architecture answer that the judge
could not tie to the retrieved chunks — exactly the line a human should read
next, which is why the runner prints the lowest scores and not only the mean.

History: the first run, on four questions, scored 0.92, and before that the
metric was sanity-checked by hand — "billing-service generates invoices every
5 minutes and is written in Rust", scored against the real chunk, came out at
**0.33**, one claim of three surviving. `--control` is that hand check,
automated and repeated on every run.

### Running it

```sh
# once: a Python 3.13 environment of its own (ragas has no 3.14 wheels). The
# main .venv stays lean; the ~35 extra packages land in .venv-evals instead.
UV_PROJECT_ENVIRONMENT=.venv-evals uv sync --python 3.13 --group evals

UV_PROJECT_ENVIRONMENT=.venv-evals uv run python -m evals.run_ragas --limit 3   # start small
UV_PROJECT_ENVIRONMENT=.venv-evals uv run python -m evals.run_ragas --check --control --record
```

PowerShell: `$env:UV_PROJECT_ENVIRONMENT = ".venv-evals"` once per shell, then
the same `uv run` commands.

| Flag | What it does |
|---|---|
| `--control` | Judges the same answers a second time with three fabricated claims appended. The score must fall — that is what proves the judge, not just the answers. |
| `--check` | Exit 1 if the clean score is below `judged.faithfulness.floor` in [baseline.json](../../evals/baseline.json), or if the control fell by less than `control_gap`. |
| `--record` | Append the run, control included, to [history.jsonl](../../evals/history.jsonl). |
| `--limit N` | Only the first N golden questions — for pricing a new judge model. |
| `--json` | Machine-readable output with per-question scores. |

Practical notes, each learned by hitting it:

- **Python ≤ 3.13.** `ragas` depends on `scikit-network`, which ships wheels
  for cp310–cp313 only; on 3.14 the install tries to compile from source and
  needs a C++ toolchain. Hence the side environment above: `uv` leaves the
  3.14 dev venv untouched and gives the judge its own 3.13 one (gitignored).
  The Docker image ships 3.13 and CI runs 3.12/3.13.
- **`langchain-community<0.4` is pinned** in the dependency group. ragas 0.4.3
  imports `langchain_community.chat_models.vertexai` at module load, and
  langchain-community 0.4 — now sunset upstream — removed it, so installing
  ragas alone fails at `import ragas`.
- **The judge is your configured model** by default, since every provider here
  is OpenAI-compatible. A stronger judge than the model under test is the
  usual advice; using the same one is cheaper and still catches gross
  hallucination.
- **Only `Faithfulness` is enabled** because it needs no embeddings, which
  keeps the judge runnable against any OpenAI-compatible endpoint — including
  ones with no embeddings API, such as a local Ollama. `ResponseRelevancy`
  and `SemanticSimilarity` need an embedding model
  (`ASSISTANT_EMBEDDING_API_KEY` / `ASSISTANT_VOYAGE_API_KEY`) and would add
  calls per question for little that the control does not already prove.
- **Reference-based metrics are unavailable** until the golden set gains
  reference answers. It currently stores *where* an answer lives, not the
  answer itself, which is all recall@k and MRR need — but `LLMContextRecall`
  and `FactualCorrectness` want a written reference to compare against.

---

## Which number to quote when

| Question you are asked | Metric | Where |
|---|---|---|
| "Does your search work?" | recall@1 / recall@5 | `run_retrieval.py --memory` |
| "How good is the ranking?" | MRR | same command |
| "Did the reranker earn its place?" | the ablation table above | `--mode dense --no-rerank`, etc. |
| "How do you know it doesn't hallucinate?" | groundedness / faithfulness | `run_ragas.py` |
| "Has quality regressed?" | the CI gate | `run_retrieval.py --memory --check` |
| "What changed over time?" | the trend log | `run_retrieval.py --trend` |
