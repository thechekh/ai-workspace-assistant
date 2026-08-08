"""HTTP API: platform info, deep health, per-session audit trail, re-indexing.

Auth: when ASSISTANT_AUTH_TOKEN is set, mutating/read-sensitive endpoints
require `Authorization: Bearer <token>`; /api/info and /api/health stay
public (the UI needs them before authenticating, and neither leaks
conversation data). Unset token = open, for zero-config dev.
"""

import logging
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from assistant.api.schemas import (
    DocumentList,
    DocumentUploadResult,
    IndexedDocument,
    SessionTurns,
    TurnRecord,
)
from assistant.config import Settings
from assistant.rag.ingest import ingest, ingest_documents
from assistant.rag.store import VectorStore

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


@router.get(
    "/sessions/{session_id}/turns",
    dependencies=[Depends(require_token)],
    response_model=SessionTurns,
)
async def session_turns(session_id: str, request: Request) -> SessionTurns:
    """Audit trail: per-turn stats + event timeline (last 50 turns of a session)."""
    turns = await request.app.state.session_store.turns(session_id)
    return SessionTurns(session_id=session_id, count=len(turns), turns=turns)


@router.get(
    "/sessions/{session_id}/turns/{turn_id}",
    dependencies=[Depends(require_token)],
    response_model=TurnRecord,
)
async def session_turn(session_id: str, turn_id: str, request: Request) -> TurnRecord:
    """One turn — what the UI's "details" panel needs, instead of all 50."""
    record = await request.app.state.session_store.turn(session_id, turn_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no turn {turn_id!r} in this session")
    return record


# --- Knowledge base: add documents at runtime -------------------------------
# The assistant ships with an EMPTY index; you add the documents it should
# answer from here (or with the ingest CLI). Nothing is seeded from the repo.

_TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst"}
_MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # per file


def _require_store(request: Request) -> VectorStore:
    store: VectorStore | None = getattr(request.app.state, "vector_store", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="no document store configured (is Qdrant reachable?)",
        )
    return store


@router.get("/documents", response_model=DocumentList)
async def list_documents(request: Request) -> DocumentList:
    """Everything currently searchable, by source document."""
    sources = await _require_store(request).list_sources()
    documents = [IndexedDocument(source=name, chunks=count) for name, count in sources]
    return DocumentList(documents=documents, total_chunks=sum(doc.chunks for doc in documents))


@router.post(
    "/documents",
    response_model=DocumentUploadResult,
    dependencies=[Depends(require_token)],
)
async def upload_documents(
    request: Request,
    files: list[UploadFile] | None = File(default=None),
    text: str | None = Form(default=None),
    source: str | None = Form(default=None),
) -> DocumentUploadResult:
    """Add documents to the knowledge base, in flight.

    Accepts uploaded text/Markdown files and/or a pasted `text` body (name it
    with `source`). Re-uploading the same source replaces its chunks, because
    chunk ids are derived from (source, index).
    """
    settings: Settings = request.app.state.settings
    store = _require_store(request)

    documents: list[tuple[str, str]] = []
    skipped: list[str] = []

    for upload in files or []:
        name = Path(upload.filename or "upload.md").name
        if Path(name).suffix.lower() not in _TEXT_SUFFIXES:
            skipped.append(f"{name} (unsupported type)")
            continue
        raw = await upload.read()
        if len(raw) > _MAX_UPLOAD_BYTES:
            skipped.append(f"{name} (larger than {_MAX_UPLOAD_BYTES // 1024 // 1024} MB)")
            continue
        try:
            documents.append((name, raw.decode("utf-8")))
        except UnicodeDecodeError:
            skipped.append(f"{name} (not UTF-8 text)")

    if text and text.strip():
        documents.append((Path(source or "pasted.md").name, text))

    if not documents:
        raise HTTPException(
            status_code=400,
            detail=f"no usable documents in the request. Skipped: {skipped}"
            if skipped
            else "provide `files` (.md/.txt/.rst) and/or a `text` field",
        )

    chunks = await ingest_documents(documents, settings, store=store)
    counts = dict(await store.list_sources())
    logger.info("indexed %d chunks from %d document(s)", chunks, len(documents))
    return DocumentUploadResult(
        indexed=[IndexedDocument(source=name, chunks=counts.get(name, 0)) for name, _ in documents],
        chunks=chunks,
        skipped=skipped,
    )


@router.delete("/documents/{source:path}", dependencies=[Depends(require_token)])
async def delete_document(source: str, request: Request) -> dict[str, object]:
    """Remove one document (every chunk of it) from the knowledge base."""
    removed = await _require_store(request).delete_source(source)
    if not removed:
        raise HTTPException(status_code=404, detail=f"no indexed document named {source!r}")
    return {"source": source, "removed_chunks": removed}


@router.post("/reindex", dependencies=[Depends(require_token)])
async def reindex(request: Request) -> dict[str, object]:
    """Re-ingest ASSISTANT_CORPUS_DIR: queued via taskiq, or inline in zero-infra mode.

    Only meaningful when a corpus folder is configured. Documents added
    through POST /api/documents live in Qdrant and need no re-indexing.
    """
    settings: Settings = request.app.state.settings
    if settings.corpus_dir is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "no ASSISTANT_CORPUS_DIR configured — this instance is filled "
                "via POST /api/documents, which needs no re-indexing"
            ),
        )
    if settings.redis_url.startswith("fakeredis://"):
        # No real Redis -> no task queue; run inline so the flow still works.
        try:
            count = await ingest(settings.corpus_dir, settings)
        except Exception as exc:
            logger.exception("inline reindex failed")
            raise HTTPException(status_code=503, detail=f"reindex failed: {exc}") from exc
        return {"mode": "inline", "chunks": count}

    from assistant.worker import reindex_docs  # lazy: pulls taskiq only when queuing

    task = await reindex_docs.kiq()
    return {"mode": "queued", "task_id": task.task_id}
