"""The two unprefixed operational endpoints, plus the dev console.

They live here rather than inline in `create_app` for one reason: every other
route in this app is declared on a module-level `APIRouter`, and having three
exceptions defined inside the app factory made `main.py` half wiring and half
routing. `main.py` now only assembles.

Unprefixed on purpose. `/healthz` is the liveness probe (is the process up?)
as distinct from `/api/health`, which is the *deep* check that talks to Redis
and Qdrant; a load balancer wants the cheap one. `/metrics` is where a
Prometheus scrape config expects to find a target.
"""

from pathlib import Path

from fastapi import APIRouter, Response
from fastapi.responses import FileResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter()

# Included by `create_app` only when ASSISTANT_DEBUG is on, so the console is
# absent — not merely unlinked — in a production configuration.
dev_router = APIRouter()

_DEV_PAGE = Path(__file__).resolve().parents[1] / "static" / "dev.html"


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness only: the process answers. See /api/health for dependencies."""
    return {"status": "ok"}


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus scrape target (counters/histograms from assistant.telemetry)."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@dev_router.get("/dev", include_in_schema=False)
async def dev_console() -> FileResponse:
    """A dependency-free HTML console for the WebSocket, for when the SPA is not built."""
    return FileResponse(_DEV_PAGE)
