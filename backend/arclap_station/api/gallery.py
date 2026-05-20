"""Gallery router: /api/gallery/*."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse

from arclap_station.api.deps import require_session
from arclap_station.audit import emit as audit_emit
from arclap_station.photos.store import get_store
from arclap_station.photos.thumbnails import generate_thumbnail

router = APIRouter(prefix="/api/gallery", tags=["gallery"])


@router.get("/list")
async def list_photos(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    date: str | None = Query(default=None),
    filter: str | None = Query(default=None, alias="filter"),
    q: str | None = Query(default=None),
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """List photos with optional filter + free-text search.

    `filter` accepts: all | uploaded | pending | starred. The cockpit's
    pill bar binds each pill to one of these values; before this the
    backend silently ignored the parameter and every pill returned
    the same data, so the operator could never narrow down to just
    the failed uploads (which is the only useful pill when a
    destination is misbehaving).

    `q` is a case-insensitive substring match against the photo's
    path / filename. Mirrors the search box in the cockpit toolbar.
    """
    store = get_store()
    items = store.list(
        limit=limit,
        offset=offset,
        date=date,
        upload_filter=filter,
        query=q,
    )
    total = store.count(upload_filter=filter, query=q)
    return {
        "total": total,
        "items": [p.to_dict() for p in items],
        "limit": limit,
        "offset": offset,
    }


@router.get("/{photo_id}/thumb")
async def thumbnail(
    photo_id: int,
    _: dict[str, Any] = Depends(require_session),
) -> FileResponse:
    photo = get_store().get(photo_id)
    if photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="photo not found")
    src = Path(photo.path)
    if not src.exists():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="photo file missing")
    thumb = generate_thumbnail(src)
    return FileResponse(thumb, media_type="image/jpeg")


@router.get("/{photo_id}/full")
async def full_photo(
    photo_id: int,
    _: dict[str, Any] = Depends(require_session),
) -> FileResponse:
    photo = get_store().get(photo_id)
    if photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="photo not found")
    src = Path(photo.path)
    if not src.exists():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="photo file missing")
    return FileResponse(src)


@router.delete("/{photo_id}")
async def delete_photo(
    photo_id: int,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    ok = get_store().delete(photo_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="photo not found")
    audit_emit("user", "gallery.delete", {"photo_id": photo_id})
    return {"ok": True}
