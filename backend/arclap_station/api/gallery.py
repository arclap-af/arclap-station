"""Gallery router: /api/gallery/*."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from arclap_station.api.deps import require_session
from arclap_station.audit import emit as audit_emit
from arclap_station.photos.store import get_store
from arclap_station.photos.thumbnails import generate_thumbnail


def _timestamped_name(captured_at: str, original: str) -> str:
    """Build a download filename prefixed with the capture timestamp.

    e.g. captured_at="2026-07-03T11:51:51.12+00:00", original="capt0022.jpg"
    -> "2026-07-03_11-51-51_capt0022.jpg". Sortable, filesystem-safe, and
    the time matches what the gallery tile shows (we reformat the raw ISO
    string rather than converting timezones). Falls back to the original
    name if captured_at is missing/unparseable.
    """
    ts = (captured_at or "").replace("T", "_")[:19].replace(":", "-")
    if len(ts) < 19:  # not a full date_time — don't prefix a partial stamp
        return original
    stem, dot, ext = original.rpartition(".")
    return f"{ts}_{stem}.{ext}" if dot else f"{ts}_{original}"

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
    # Name the download after the capture timestamp so a saved file is
    # self-identifying (the pixels are untouched). Browsers ignore this
    # Content-Disposition for the lightbox <img>, so viewing still works.
    return FileResponse(src, filename=_timestamped_name(photo.captured_at, src.name))


class BulkDeleteBody(BaseModel):
    ids: list[int] | None = None
    all: bool = False
    filter: str | None = None
    query: str | None = None


@router.post("/bulk-delete")
async def bulk_delete(
    body: BulkDeleteBody,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """Delete many photos in ONE request. `all=true` deletes every photo
    matching the current filter/search (not just the visible first page —
    the old client-side loop only touched what was loaded); otherwise
    deletes the given ids."""
    store = get_store()
    if body.all:
        deleted = store.delete_matching(upload_filter=body.filter, query=body.query)
    else:
        deleted = sum(1 for pid in (body.ids or []) if store.delete(pid))
    audit_emit("user", "gallery.bulk_delete", {"count": deleted, "all": body.all})
    return {"deleted": deleted}


class StarBody(BaseModel):
    starred: bool = True


@router.post("/{photo_id}/star")
async def star_photo(
    photo_id: int,
    body: StarBody,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """Star / unstar a photo. Starred photos survive every retention
    sweep (including emergency) — this is the operator's only long-term
    keep mechanism, so it must actually persist."""
    ok = get_store().set_starred(photo_id, body.starred)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="photo not found")
    audit_emit("user", "gallery.star" if body.starred else "gallery.unstar", {"photo_id": photo_id})
    return {"ok": True, "starred": body.starred}


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
