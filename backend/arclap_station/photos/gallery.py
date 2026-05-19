"""Filesystem walker — reconciles /media/sdcard/photos with the SQLite store.

Used at boot and on demand to backfill photos captured while the agent was
offline.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from arclap_station.config import get_settings
from arclap_station.photos.store import PhotoStore, get_store

log = logging.getLogger(__name__)

PHOTO_SUFFIXES = {".jpg", ".jpeg", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".dng"}


def walk_photos(root: Path | None = None) -> Iterable[Path]:
    root = root or get_settings().paths.photos
    if not root.exists():
        return []
    return (p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in PHOTO_SUFFIXES)


def reconcile(store: PhotoStore | None = None) -> int:
    """Scan filesystem and add any new photos to the store. Returns count added."""
    store = store or get_store()
    added = 0
    for path in walk_photos():
        if store.by_path(path) is not None:
            continue
        try:
            stat = path.stat()
            captured_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
            store.register(path, size_bytes=stat.st_size, captured_at=captured_at)
            added += 1
        except OSError as exc:
            log.warning("could not register %s: %s", path, exc)
    return added
