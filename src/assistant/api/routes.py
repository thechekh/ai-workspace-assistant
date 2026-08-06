"""HTTP API: platform info + document re-indexing.

Auth: when ASSISTANT_AUTH_TOKEN is set, mutating endpoints require
`Authorization: Bearer <token>`; /api/info stays public (the UI needs it
before authenticating). Unset token = open, for zero-config dev.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from assistant.config import Settings
from assistant.rag.ingest import ingest

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


def require_token(request: Request) -> None:
    settings: Settings = request.app.state.settings
    if settings.auth_token is None:
        return
    expected = f"Bearer {settings.auth_token.get_secret_value()}"
    if request.headers.get("Authorization") != expected:
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")


@router.get("/info")
async def info(request: Request) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    return {
        "backends": sorted(request.app.state.agents),
        "default_backend": request.app.state.default_backend,
        "llm_provider": settings.llm_provider,
        "embedding_provider": settings.embedding_provider,
        "retrieval_mode": settings.retrieval_mode,
        "collection": settings.qdrant_collection,
        "auth_required": settings.auth_token is not None,
    }


@router.post("/reindex", dependencies=[Depends(require_token)])
async def reindex(request: Request) -> dict[str, object]:
    """Re-ingest the docs corpus: queued via taskiq, or inline in zero-infra mode."""
    settings: Settings = request.app.state.settings
    if settings.redis_url.startswith("fakeredis://"):
        # No real Redis -> no task queue; run inline so the flow still works.
        try:
            count = await ingest(Path("docs_corpus"), settings)
        except Exception as exc:
            logger.exception("inline reindex failed")
            raise HTTPException(status_code=503, detail=f"reindex failed: {exc}") from exc
        return {"mode": "inline", "chunks": count}

    from assistant.worker import reindex_docs  # lazy: pulls taskiq only when queuing

    task = await reindex_docs.kiq()
    return {"mode": "queued", "task_id": task.task_id}
