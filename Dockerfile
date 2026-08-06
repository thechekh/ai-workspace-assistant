# Multi-stage build: Vue SPA -> Python runtime (served by FastAPI).
# The same image runs the api, the taskiq worker, and the scheduler —
# compose picks the command per service.

# --- frontend build -----------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- backend runtime ----------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependencies first (cached layer), project second
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ src/
COPY docs_corpus/ docs_corpus/
COPY evals/ evals/
RUN uv sync --frozen --no-dev
COPY --from=frontend /app/frontend/dist frontend/dist

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "assistant.main:app", "--host", "0.0.0.0", "--port", "8000"]
