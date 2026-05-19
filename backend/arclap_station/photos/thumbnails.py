"""Pillow-based thumbnail generator with on-disk cache."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from arclap_station.config import get_settings

THUMB_MAX_WIDTH = 400
THUMB_QUALITY = 78


def thumbnail_path(source: Path) -> Path:
    digest = hashlib.sha1(str(source.resolve()).encode("utf-8"), usedforsecurity=False).hexdigest()
    return get_settings().paths.thumbnails / f"{digest}.jpg"


def generate_thumbnail(source: Path, force: bool = False) -> Path:
    """Generate a 400px-wide JPEG thumbnail; returns the cached path."""
    target = thumbnail_path(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        if target.stat().st_mtime >= source.stat().st_mtime:
            return target
    with Image.open(source) as img:
        img = img.convert("RGB")
        ratio = THUMB_MAX_WIDTH / max(img.width, 1)
        if ratio < 1:
            new_size = (THUMB_MAX_WIDTH, max(1, int(img.height * ratio)))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        img.save(target, format="JPEG", quality=THUMB_QUALITY, optimize=True)
    return target
