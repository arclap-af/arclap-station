"""Upload queue router: /api/queue/*."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Query

from arclap_station.api.deps import require_session
from arclap_station.audit import emit as audit_emit
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
    # drain_once() does blocking network I/O — run it off the event loop
    # so the whole cockpit doesn't freeze for the duration of an upload.
    processed = await asyncio.to_thread(get_queue().drain_once)
    return {"processed": processed}


@router.post("/retry-failed")
async def retry_failed(
    photo_id: int | None = Query(default=None),
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """Requeue failed / permanently-failed uploads (all, or one photo).

    The recovery path that was missing — after a sustained outage the
    operator can re-drive the backlog instead of losing it."""
    n = get_queue().requeue_failed(photo_id=photo_id)
    audit_emit("user", "upload.requeued", {"count": n, "photo_id": photo_id})
    return {"requeued": n}
