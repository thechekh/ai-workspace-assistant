# Coding Standards

## Python

- Python 3.12+, projects managed with uv (`pyproject.toml` plus lockfile).
- Lint and format with ruff; type check with pyright in standard mode.
- src layout; Pydantic v2 models at every I/O boundary.
- FastAPI for HTTP services; endpoints stay thin, domain logic lives in plain
  modules that are unit-testable without the framework.
- Async first: any network or disk I/O uses async clients.

## Testing

The pyramid: many fast unit tests, a focused set of integration tests, and a
handful of end-to-end smoke tests. Tests run with pytest; external
dependencies are faked (fakeredis, in-memory Qdrant, scripted LLM responses).
A pull request must keep coverage of changed code and add a regression test
for every fixed bug.

## Frontend

Vue 3 with the Composition API and TypeScript in strict mode. State lives in
Pinia stores; server communication is typed end to end against the backend
protocol models. Components stay small; shared logic lives in composables.

## Commits and pull requests

We follow Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`,
`chore:`). Pull requests are small and focused — under ~400 changed lines
where possible. Every PR needs one approving review; the review SLA is one
business day. CI (lint, types, tests) must be green before merge; merges are
squash merges with the PR title as the commit message.

## API guidelines

REST resources are plural nouns (`/invoices/{id}`), errors use RFC 9457
problem+json, all list endpoints paginate with cursor parameters, and
breaking changes require a new versioned route (`/v2/...`) with a deprecation
window for the old one.
