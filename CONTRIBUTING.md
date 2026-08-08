# Contributing

## Setup

```sh
uv sync                                   # backend deps + venv
cd frontend && npm install && cd ..       # frontend deps
uv run pre-commit install                 # format/lint on commit (once)
cp .env.example .env                      # everything works unset — see the file
```

The app runs with **zero** external services or API keys: set
`ASSISTANT_REDIS_URL=fakeredis://` and leave `ASSISTANT_LLM_PROVIDER=fake`.
See [docs/handbook/02-getting-started.md](docs/handbook/02-getting-started.md)
for the four run modes.

## Before you push

The same gates run in CI, so run them locally first:

```sh
uv run pytest -q            # 212 tests, offline, ~15s
uv run ruff check . && uv run ruff format --check .
uv run pyright
cd frontend && npm run lint && npm run typecheck && npm run test:run
```

`pytest -m "not slow"` skips the tests that spawn real MCP subprocesses.

## House rules

- **Offline-first.** Anything new must work with no keys and no containers.
  That is what keeps the suite fast and free; reach for a fake, not a mock of
  a network call.
- **Instrument at the seam, not per feature.** Tools go through `Tool.run`,
  LLM calls through `InstrumentedLLM`. Adding a tool should not mean adding
  telemetry.
- **A failure the user sees needs a message they can act on.** Provider
  errors are mapped in `llm/errors.py`; tool crashes become error *results*,
  never exceptions that kill a turn.
- **Config over code branches.** Providers, backends, embedders and
  retrieval mode are all settings, not `if` statements in business logic.
- **If you change a number the docs quote** (test counts, backend line
  counts, eval scores), `tests/test_docs_consistency.py` will tell you which
  documents to update. Broken doc links fail `tests/test_docs_links.py`.

## Where things live

Documentation is one tree under [docs/](docs/README.md) — handbook (how to
operate it), theory (concepts from zero), reference (single subjects),
project (roadmap, decisions, workshop). Prose belongs there, not in the
repo root.
