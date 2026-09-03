"""HTTP API: platform info, deep health, per-session audit trail, re-indexing.

Auth: when ASSISTANT_AUTH_TOKEN is set, mutating/read-sensitive endpoints
require `Authorization: Bearer <token>`; /api/info and /api/health stay
public (the UI needs them before authenticating, and neither leaks
conversation data). Unset token = open, for zero-config dev.

Writes are additionally rate limited (see `rate_limit.py`): indexing is the
one path here that costs real work per call.
"""

import hashlib
import logging
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from assistant.api.rate_limit import RateLimiter
from assistant.api.schemas import (
    DocumentList,
    DocumentUploadResult,
    IndexedDocument,
    SessionList,
    SessionMessages,
    SessionTurns,
    TurnRecord,
)
from assistant.config import Settings
from assistant.rag.ingest import ingest_documents
from assistant.rag.store import VectorStore
from assistant.telemetry import ERRORS_TOTAL, RATE_LIMITED_TOTAL

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def require_token(request: Request) -> None:
    settings: Settings = request.app.state.settings
    if settings.auth_token is None:
        return
    expected = f"Bearer {settings.auth_token.get_secret_value()}"
    # compare_digest, not ==: a plain comparison returns as soon as two bytes
    # differ, which leaks the shared secret one character at a time to anyone
    # who can time the endpoint.
    if not secrets.compare_digest(request.headers.get("Authorization", ""), expected):
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")


async def limit_writes(request: Request) -> None:
    """Throttle the *indexing* endpoints, keyed by caller.

    Deliberately not applied to `DELETE /api/documents/{source}`: the budget
    this protects is embedding work, deleting costs none of it, and throttling
    the remedy means someone who over-uploads cannot clean up for an hour.
    Live testing found exactly that — the limiter refused the cleanup.

    Identity is the bearer token when one is configured (the deployed case:
    every client behind a shared proxy IP would otherwise share one bucket),
    and the peer address otherwise.
    """
    settings: Settings = request.app.state.settings
    limiter: RateLimiter = request.app.state.rate_limiter
    if settings.auth_token is not None:
        # Hashed, because this ends up in a Redis key name: a slice of the raw
        # bearer token would put part of the secret somewhere it can be dumped
        # with KEYS, logged by a slowlog, or read from an RDB snapshot.
        credential = request.headers.get("Authorization", "")
        identity = hashlib.sha256(credential.encode()).hexdigest()[:16]
    else:
        identity = request.client.host if request.client else "unknown"
    decision = await limiter.check(
        "writes", identity, limit=settings.rate_limit_uploads_per_hour, window_seconds=3600
    )
    if not decision.allowed:
        ERRORS_TOTAL.labels(kind="rate_limited").inc()
        RATE_LIMITED_TOTAL.labels(bucket="writes").inc()
        raise HTTPException(
            status_code=429,
            detail=decision.message("indexing requests"),
            headers={"Retry-After": str(decision.retry_after)},
        )


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


@router.get("/sessions", dependencies=[Depends(require_token)], response_model=SessionList)
async def list_sessions(request: Request, limit: int = 30) -> SessionList:
    """Recent conversations, newest first — the sidebar's data.

    Auth-guarded like the audit endpoints: the previews are conversation
    content, unlike /api/info and /api/health.
    """
    sessions = await request.app.state.session_store.recent(max(1, min(limit, 100)))
    return SessionList(sessions=sessions)


@router.delete("/sessions/{session_id}", dependencies=[Depends(require_token)])
async def forget_session(session_id: str, request: Request) -> dict[str, object]:
    """Delete one conversation: history, audit trail and rolling summary."""
    if not await request.app.state.session_store.forget(session_id):
        raise HTTPException(status_code=404, detail=f"no session {session_id!r}")
    return {"session_id": session_id, "deleted": True}


@router.get(
    "/sessions/{session_id}/messages",
    dependencies=[Depends(require_token)],
    response_model=SessionMessages,
)
async def session_messages(session_id: str, request: Request) -> SessionMessages:
    """Reopen a conversation: the transcript the sidebar restores into the UI.

    The WebSocket resumes a session for the *model* (history goes into the
    prompt) but never replays it to the client, so without this a reopened
    conversation would look empty while the assistant remembered it.
    """
    messages = await request.app.state.session_store.history(session_id)
    return SessionMessages(session_id=session_id, messages=messages)


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
    dependencies=[Depends(require_token), Depends(limit_writes)],
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
