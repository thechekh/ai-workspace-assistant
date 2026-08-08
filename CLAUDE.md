# CLAUDE.md

Guidance for AI coding agents working in this repository.

## What this is

An internal AI assistant for engineers: FastAPI + WebSocket streaming chat,
RAG over Qdrant, three interchangeable agent runtimes, MCP tool servers, and
end-to-end observability. Read [docs/README.md](docs/README.md) first — the
handbook explains how to run it, the theory course explains the concepts.

## Commands

```sh
uv run pytest -q                    # 212 tests, offline, no keys, ~15s
uv run pytest -m "not slow"         # skips real MCP subprocess spawns
uv run ruff check . && uv run ruff format .
uv run pyright
uv run uvicorn assistant.main:app --reload
cd frontend && npm run lint && npm run typecheck && npm run test:run
```

Everything runs with `ASSISTANT_LLM_PROVIDER=fake` and
`ASSISTANT_REDIS_URL=fakeredis://` — no network, no containers, no cost.
Prefer that for development; use a real provider only when evaluating model
behaviour.

## Invariants worth preserving

- **Offline-first.** A change that requires a key or a container to test is
  a change that will not be tested.
- **One seam per concern.** `Tool.run` owns tool telemetry and guards;
  `InstrumentedLLM` owns LLM telemetry; `llm/errors.py` owns provider-error
  classification. Add behaviour there, not in each call site.
- **Three backends, one contract.** Anything added to one agent runtime must
  hold for all three — `tests/test_fake_parity.py` and the ×3-parametrized WS
  suite exist because a hand-copied fake silently drifted once.
- **Errors reach the user as actionable text**, and a tool crash becomes an
  error *result*, never an exception that ends the turn.
- **The knowledge base starts empty.** Do not reintroduce seed data; it is
  filled at runtime through `POST /api/documents`. `evals/corpus/` is the
  retrieval test fixture only.

## Docs are tested

`tests/test_docs_links.py` fails on broken relative links, stray prose
outside `docs/`, and an index that stops covering a folder.
`tests/test_docs_consistency.py` fails when documents contradict each other
or the code about test counts, backend line counts, golden-set size, or
retrieval scores. If you change one of those numbers, the failure names the
files to update.

## Gotchas found the hard way

- `retry-after: 0` is valid and means "retry now" — never test provider
  header values for truthiness.
- llama models sometimes emit tool calls as plain text (`<function…>`) or
  trip Groq's `tool_use_failed`; `llm/client.py` retries and salvages both.
  Do not "simplify" that away.
- `WebSocketDisconnect` subclasses `Exception`; catch it explicitly before
  any broad handler or routine disconnects pollute the error metrics.
- Importing `assistant.main` must stay side-effect free — the app is built
  lazily via module `__getattr__` so tests do not read a developer's `.env`.
