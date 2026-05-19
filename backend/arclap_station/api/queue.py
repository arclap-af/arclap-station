"""Upload queue router: /api/queue/*."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from arclap_station.api.deps import require_session
from arclap_station.uploaders.queue import get_queue

router = APIRouter(prefix="/api/queue", tags=["queue"])


@router.get("/list")
async def list_queue(
    state: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _: dict[str, Any] = Depends(require_session),
) -> list[dict[str, Any]]:
    return [i.to_dict() for i in get_queue().list(state=state, limit=limit)]


@router.get("/stats")
async def queue_stats(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    return {
        "stats": get_queue().stats(),
        "pending_depth": get_queue().pending_depth(),
    }


@router.post("/drain-once")
async def drain_once(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    return {"processed": get_queue().drain_once()}
