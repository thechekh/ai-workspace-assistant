"""HTTP API: platform info, deep health, per-session audit trail, re-indexing.

Auth: when ASSISTANT_AUTH_TOKEN is set, mutating/read-sensitive endpoints
require `Authorization: Bearer <token>`; /api/info and /api/health stay
public (the UI needs them before authenticating, and neither leaks
conversation data). Unset token = open, for zero-config dev.
"""

import logging
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from assistant.config import Settings
from assistant.rag.ingest import ingest

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


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


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    """Deep health: ping every dependency with latency — unlike the /healthz
    liveness probe, this answers "can a chat turn actually succeed right now?"."""
    settings: Settings = request.app.state.settings
    components: dict[str, dict[str, object]] = {}

    started = time.perf_counter()
    try:
        await request.app.state.redis.ping()
        components["redis"] = {"status": "ok", "latency_ms": _ms(started)}
    except Exception as exc:
        components["redis"] = {"status": "error", "detail": str(exc)}

    qdrant = getattr(request.app.state, "qdrant", None)
    if qdrant is None:
        # Tests / embedded mode inject a retriever, so there is no live client.
        components["qdrant"] = {"status": "skipped", "detail": "external retriever injected"}
    else:
        started = time.perf_counter()
        try:
            result = await qdrant.count(settings.qdrant_collection)
            components["qdrant"] = {
                "status": "ok",
                "latency_ms": _ms(started),
                "collection": settings.qdrant_collection,
                "points": result.count,
            }
        except Exception as exc:
            components["qdrant"] = {"status": "error", "detail": str(exc)}

    # No live LLM call — deep health must stay free and rate-limit-safe.
    # If a hosted provider's key were missing, startup would have failed.
    components["llm"] = {
        "status": "ok",
        "provider": settings.llm_provider,
        "model": settings.llm_model,
    }

    registry = getattr(request.app.state, "mcp_registry", None)
    if registry is None:
        components["mcp"] = {"status": "disabled"}
    else:
        # Unreachable servers are skipped at startup, so "enabled but nothing
        # connected" must read as degraded — that is precisely the case where
        # every MCP tool is missing from the agent.
        expected = registry.expected_servers
        connected = registry.connected_servers
        missing = [name for name in expected if name not in connected]
        components["mcp"] = {
            "status": "ok" if not missing else "error",
            "tools": list(request.app.state.mcp_tool_names),
            "servers_connected": f"{len(connected)}/{len(expected)}",
            **({"unreachable": missing} if missing else {}),
        }

    degraded = any(component["status"] == "error" for component in components.values())
    return {"status": "degraded" if degraded else "ok", "components": components}


@router.get("/sessions/{session_id}/turns", dependencies=[Depends(require_token)])
async def session_turns(session_id: str, request: Request) -> dict[str, object]:
    """Audit trail: per-turn stats + event timeline (last 50 turns of a session)."""
    turns = await request.app.state.session_store.turns(session_id)
    return {"session_id": session_id, "count": len(turns), "turns": turns}


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
