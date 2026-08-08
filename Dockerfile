# Multi-stage build: Vue SPA -> Python runtime (served by FastAPI).
# The same image runs the api, the taskiq worker, and the scheduler —
# compose picks the command per service.

# --- frontend build -----------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
# Only the sources the build needs — never the host's node_modules/dist
# (.dockerignore also excludes them, belt and braces).
COPY frontend/index.html frontend/vite.config.ts frontend/tsconfig.json ./
COPY frontend/public/ ./public/
COPY frontend/src/ ./src/
RUN npm run build

# --- backend runtime ----------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependencies first (cached layer), project second.
# README.md is required: pyproject declares it as the package readme, so the
# hatchling build of this project fails without it.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ src/
COPY docs_corpus/ docs_corpus/
COPY evals/ evals/
RUN uv sync --frozen --no-dev
COPY --from=frontend /app/frontend/dist frontend/dist

# Run as an unprivileged user (after everything is installed and owned by root)
RUN useradd --system --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8000
# urlopen raises (non-zero exit) on any non-2xx or connection failure.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD uv run --no-sync python -c "import urllib.request as r; r.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["uv", "run", "--no-sync", "uvicorn", "assistant.main:app", "--host", "0.0.0.0", "--port", "8000"]
