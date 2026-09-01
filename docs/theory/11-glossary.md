# 11 — Glossary

One line per term. Numbers in parentheses are the theory chapter that
explains it properly; `sec` points at
[reference/security.md](../reference/security.md) instead.

Every term here is one this project actually uses — in the code, the docs, or
both. If a reviewer asks "what do you mean by X", X should be on this page.

## Models and the API

- **LLM** — large language model; predicts the next token, repeatedly (01).
- **Inference** — one run of the model over a prompt to produce output; what you pay for (01).
- **Token** — the model's text unit, ≈4 characters; pricing and limits are counted in tokens (01).
- **Prompt tokens / completion tokens** — what you sent vs what the model wrote; priced differently, and completion is usually dearer (01, 04).
- **Context window** — everything the model can see in one request; its entire working memory (01).
- **System prompt** — the operator's standing instructions to the model: persona, rules, tools (01).
- **Message roles** — `system` / `user` / `assistant` / `tool`; the four speakers in a prompt (01, 04).
- **Streaming** — receiving the answer token-by-token as it's generated (01, 08).
- **Stateless API** — the provider remembers nothing between requests; history must be resent (01, 07).
- **Provider** — whoever serves the model over an API (OpenAI, Ollama, Gemini) (01).
- **OpenAI-compatible API** — the de-facto standard chat API shape many providers implement (01).
- **Chat Completions API** — that shape's endpoint: a list of messages in, a message out (01).
- **Model size (8B / 70B)** — parameter count; bigger follows instructions and picks tools more reliably, and costs more (01, 04).
- **FakeLLM** — our deterministic offline model stand-in; $0, no network, same behavior every run (01, 09).
- **Hallucination** — a confident fabricated answer; mitigated by grounding in retrieved docs (01, 03).
- **Prompt injection** — hostile instructions hidden in content the model reads; treated as untrusted input (sec).

## Embeddings and vector search

- **Embedding** — a vector representing a text's meaning; similar meaning → nearby vectors (02).
- **Vector** — the list of numbers itself; a point in a high-dimensional space (02).
- **Dimensionality** — how many numbers per vector; ours is 512, real models use 768–3072 (02).
- **Dense vector** — semantic embedding: every slot carries a little meaning (02).
- **Sparse vector** — keyword-style vector: non-zero only for tokens the text contains (02).
- **Named vectors** — Qdrant storing several vectors per point under names, so one collection holds both dense and sparse (02).
- **Cosine similarity** — angle-based closeness of two vectors; the "how related" score (02).
- **Feature hashing** — mapping tokens to fixed vector slots by hash; our offline `hash-512` embedder (02).
- **Semantic search** — matching by meaning rather than shared words; what dense vectors buy you (02).
- **Lexical matching** — matching by the literal tokens present; what sparse vectors do (02).
- **Vocabulary mismatch** — the question says "linter", the doc says "lint"; the gap semantic search closes (02, 03).
- **Vector database** — a store optimized for nearest-neighbor search over vectors; ours is Qdrant (02).
- **HNSW** — the standard approximate nearest-neighbor index inside vector DBs (02).

## RAG

- **RAG** — retrieval-augmented generation: retrieve relevant chunks, then answer from them (03).
- **Retrieval** — the search step: question in, candidate chunks out (03).
- **Fine-tuning** — retraining weights on your data; the alternative to RAG we deliberately did not use (03).
- **Corpus** — the body of source documents; ours lives in `evals/corpus/` as a test fixture only (03).
- **Knowledge base** — what is actually indexed and searchable right now; starts empty, filled at runtime (03).
- **Ingestion** — the pipeline that turns documents into indexed vectors: parse → chunk → embed → upsert (03).
- **Chunking** — splitting documents into retrieval-sized pieces; ours is heading-aware (03).
- **Breadcrumb** — the heading path ("Service Catalog > billing-service") prefixed to each chunk (03).
- **Idempotent ingest** — re-ingesting replaces a source rather than duplicating or orphaning it (03).
- **Hybrid search** — running dense + sparse retrieval and fusing the rankings (02, 03).
- **Fusion** — merging two ranked lists into one (02).
- **RRF** — reciprocal rank fusion; combines ranked lists using ranks only: Σ 1/(k+rank) (02).
- **Reranker** — a second, more careful pass that reorders the top retrieval candidates (02, 03).
- **Top-k** — how many results you keep; we fetch ~20 candidates, rerank, and return 4 (03).
- **Relevance gate** — dropping results that share no meaningful token with the query, so "nothing found" is trustworthy (03).
- **Grounding** — forcing answers to come from retrieved evidence, with citations (03).
- **Citation** — naming the source file an answer came from, so a reader can check it (03).

## Agents and tool calling

- **Tool / function calling** — the model *requests* a function call as JSON; the app executes it (04).
- **JSON Schema** — the typed description of a tool's arguments (04).
- **Agent** — the loop: model → tool call → result → model, until a final answer (04).
- **Agentic loop** — that same loop viewed as a control structure; the model decides, the app acts (04).
- **ReAct** — the reason+act pattern the loop implements (04).
- **LLM step** — one model round trip inside a turn; a tool-using turn takes at least two (04).
- **Tool result** — the executed tool's output, appended to the conversation for the next step (04).
- **Tool registry** — the allowlist of callable tools; an unknown name is refused, never executed (04, sec).
- **Duplicate-call guard** — the same call twice in one turn returns a pointer to the first result instead of re-running (04).
- **max_iterations / recursion limit** — the bound that stops a runaway agent loop (04, 05).
- **AgentBackend** — our protocol all three runtimes implement; one contract, three engines (05).
- **Pydantic AI** — typed agent framework from the Pydantic team; backend B (05).
- **LangGraph** — state-graph agent framework from the LangChain ecosystem; backend C (05).
- **StateGraph** — LangGraph's model of an agent: nodes and edges rather than a `while` loop (05).
- **Checkpointing** — LangGraph persisting graph state per thread; durable/resumable runs (05).

## MCP — Model Context Protocol

- **MCP** — Model Context Protocol; the open standard for exposing tools to AI apps (06).
- **MCP server / client** — the program exposing tools / the app consuming them (06).
- **stdio transport** — client spawns the MCP server as a subprocess, talks over stdin/stdout (06).
- **Streamable HTTP transport** — MCP over a remote HTTP endpoint (06).
- **Tool discovery** — asking a server what it can do (`list_tools`) instead of hardcoding it (06).
- **Tool namespacing** — `code__search_code`: server name prefixed so tools never collide (06).
- **Graceful degradation** — unreachable MCP server → warning + skip, agent runs with the rest (06).

## Memory and context management

- **Session** — one conversation, identified by `session_id`, resumable across reconnects (07, 08).
- **Transcript** — the full stored message history of a session; the audit record (07).
- **Conversation memory** — the layer that decides how much of that transcript the model actually sees (07).
- **Short-term memory** — this conversation, in Redis with a TTL (07).
- **Long-term memory** — knowledge that outlives a conversation; here, the vector database (07).
- **Rolling summary** — persisted digest of older turns; keeps prompts bounded (07).
- **Context budget** — the character ceiling past which old turns get folded into that summary (07).

## Evaluation and quality

- **Eval** — a measured quality check (vs a pass/fail unit test) (09).
- **Golden set** — annotated question→answer-location pairs used to score retrieval (03, 09).
- **recall@k** — fraction of questions whose correct chunk appears in the top k results; blind to rank within the window ([metrics](../reference/metrics.md)) (03).
- **MRR** — mean reciprocal rank; averages 1/rank, so rank 1 counts double rank 2 ([metrics](../reference/metrics.md)) (03).
- **Baseline** — the recorded numbers a change is compared against; ours live in `evals/baseline.json` (09).
- **Ablation** — turning one stage off to measure what it contributes (dense vs hybrid vs reranked) (03, 09).
- **Regression gate** — CI failing the build when a measured number drops below the baseline (09).
- **Benchmark** — a measurement run; here always reproducible from the repository, never quoted from memory (09).
- **Determinism** — same input, same output; what fakes and fixed seeds buy the test suite (09).
- **LLM-as-judge** — scoring answers with another model; used on demand via Ragas, never in CI ([metrics](../reference/metrics.md)) (09).
- **Groundedness / faithfulness** — the share of an answer's claims supported by the retrieved context; hallucination, measured ([metrics](../reference/metrics.md)) (03, 09).
- **Ragas** — the LLM-judged RAG evaluation library behind that metric; an optional dependency group (09).

## Observability and LLMOps

- **Observability** — being able to answer "what happened in that turn?" after the fact (09).
- **Trace / span** — one request's timed tree of nested operations, and a single node of it (09).
- **OpenTelemetry (OTel / OTLP)** — the vendor-neutral standard for emitting and shipping traces (09).
- **Waterfall** — the trace visualised as nested bars; where the time actually went (09).
- **Correlation id** — the id (`session_id`, `turn_id`) stamped on every log line of one turn (09).
- **Token accounting** — summing prompt/completion tokens per turn; real when the provider reports them, estimated otherwise (09).
- **Cost accounting** — converting those tokens to money at listed prices (09).
- **Time to first token (TTFT)** — latency until the first character appears; the number a user feels (09).
- **Throughput limits (TPM / TPD)** — tokens per minute and per day a provider allows; the free tier's real constraint (04, 09).
- **Rate limiting / 429 / backoff** — being told to slow down, and waiting the requested time before retrying (04, 09).
- **Prometheus** — the metrics database scraping `/metrics` for counters and histograms (09).
- **Audit trail** — the stored per-turn record — stats plus timeline — that makes a turn replayable (09).
- **Logfire / Langfuse** — our two OTel backends: app view / LLM view (09).

## Serving and infrastructure

- **WebSocket** — persistent bidirectional connection; carries our typed chat frames (08).
- **SSE** — server-sent events; one-way streaming alternative we didn't need (08).
- **Cancellation** — stopping a turn already in flight; the reason the protocol has to be bidirectional (08).
- **Backpressure** — not producing faster than the consumer can take; why a turn is cancelled when its socket dies (08).
- **Async / concurrency** — one process serving many conversations by awaiting I/O rather than threading (08, 10).
- **Qdrant** — the vector database holding the knowledge base (02, 10).
- **Redis** — the store holding sessions, summaries, audit trails and rate-limit windows (07, 10).
- **taskiq** — async task queue running our background re-index + nightly cron (10).
- **uv / ruff / pyright** — modern Python toolchain: packaging / lint+format / type checking (10).
- **Compose profile** — optional service group; `--profile app` starts the full platform (10).

## Safety and guardrails

- **Untrusted model output** — treating what the model returns as input to validate, never as code to run (sec).
- **Least privilege** — every tool read-only; nothing writes files, runs shells or mutates state (sec).
- **Bearer token** — the shared secret guarding write and conversation endpoints when configured (sec).
- **SSRF** — server-side request forgery: making the server fetch an internal address on an attacker's behalf (sec).
- **Path traversal** — escaping an allowed directory with `../`; refused by resolving and checking the root (sec).
- **Content sanitisation** — stripping instruction-like content from ingested documents; a known gap here (sec).
- **Data residency** — which jurisdiction your prompts are processed in; a reason the provider is a config value (sec).

## Deliberately not used

Terms you will be asked about that this project does **not** use. Knowing why
is worth as much as knowing the term — "we don't, because…" is a better answer
than a blank look, and each of these is a real decision rather than an
oversight.

- **Temperature / top-p (sampling parameters)** — knobs controlling how randomly the model picks each token. Never set here: provider defaults are taken as-is, because the eval harness measures *retrieval*, and pinning generation randomness would imply a determinism the API does not actually offer. Reproducibility comes from `FakeLLM` instead (01, 09).
- **Dot product / inner product** — the other common vector similarity metric. The collection is configured `Distance.COSINE`, but `hash-512` L2-normalizes every vector, and for unit-length vectors cosine and dot product produce *identical rankings*. Cosine is declared because it stays correct if an un-normalized embedder is swapped in (02).
- **Vector quantization** — compressing stored vectors (scalar/product/binary) to trade a little accuracy for much less memory. Qdrant supports it; not enabled, because it earns its keep at millions of vectors and this knowledge base holds tens (02, 10).
- **Precision / accuracy** — the classification metrics people expect to hear. Not measured, deliberately: retrieval here feeds *one* answer, so what matters is whether the right chunk is near the top, which is what `recall@k` and `MRR` capture. Precision would score a system that returns everything (03, 09).
