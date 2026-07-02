"""Burn-in watermark for captured photos.

Adds a small bottom-right overlay with the station's serial, site
label, and capture timestamp. Used as proof-of-capture for legal /
insurance deliverables — the timestamp is from system clock (NTP-synced
per v0.7), and the serial is from /proc/cpuinfo so it can't be spoofed
after the fact without re-rendering the whole frame.

Pillow only — no extra deps. Opt-in via station config (`watermark`
field in station.json) so customers who want pristine RAW-bit-equal
output can disable it.

Auto-rotate is also handled here because once we've decoded the image
we may as well bake the EXIF Orientation into pixels so every viewer
(thumbnail tools, S3 web preview, etc) shows the photo correctly.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from arclap_station.station_config import get_station_store

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

log = logging.getLogger(__name__)


def apply_watermark_and_rotate(path: Path, *, force: bool = False) -> bool:
    """Mutate the JPEG at `path` in-place.

    - If station_config.watermark is true, bake "serial · site · ts"
      bottom-right.
    - Always honour EXIF Orientation if present, rotating the pixels
      and clearing the tag so downstream viewers don't double-rotate.

    Returns True if the file was modified. Never raises — capture
    has already succeeded, so cosmetic edits must not fail the chain.
    """
    try:
        from PIL import Image, ImageOps  # noqa: PLC0415
    except ImportError:
        return False

    cfg = get_station_store().load()
    want_wm = force or _wants_watermark(cfg)
    try:
        with Image.open(str(path)) as img:
            # ImageOps.exif_transpose() reads Orientation and applies
            # the rotation, returning a new image with the tag stripped.
            # Without in_place it always returns an image (never None) on
            # our pinned Pillow ≥11, so no None-guard is needed.
            rotated = ImageOps.exif_transpose(img)
            changed = rotated.size != img.size or rotated.getexif() != img.getexif()
            if want_wm:
                _draw_watermark(rotated, cfg)
                changed = True
            if not changed:
                return False
            # Preserve EXIF (minus orientation) and JPEG quality.
            exif_bytes = rotated.info.get("exif")
            save_kwargs = {"quality": 95, "optimize": True}
            if exif_bytes:
                save_kwargs["exif"] = exif_bytes
            rotated.save(str(path), "JPEG", **save_kwargs)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("watermark/rotate failed on %s: %s", path, exc)
        return False


def _wants_watermark(cfg: object) -> bool:
    """Should this capture get a burned-in timestamp watermark?

    Default: True. Construction-site stations need every photo to
    carry a visible timestamp + station id for legal / insurance
    deliverables — that's the entire reason this code exists. The
    operator can still turn it off explicitly by setting
    `watermark: false` in station.json (or via Settings → General).
    """
    try:
        # Honour an explicit setting if present (True or False).
        v = getattr(cfg, "watermark", None)
        if v is None:
            return True
        return bool(v)
    except AttributeError:
        return True


def _draw_watermark(img: PILImage, cfg: object) -> None:
    from PIL import ImageDraw, ImageFont  # noqa: PLC0415

    serial = getattr(cfg, "serial", "") or "unknown"
    site = getattr(cfg, "site", "") or ""
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    text = f"{serial[:12]} · {site[:24]} · {ts}".strip(" ·")

    w, h = img.size
    # Font sized relative to image height so 6720×4480 and 1920×1080
    # both render reasonably.
    font_size = max(18, h // 100)
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()

    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    pad = font_size // 2
    x = w - tw - pad * 2 - 12
    y = h - th - pad * 2 - 12
    # Translucent dark background for legibility on any image.
    draw.rectangle(
        [x, y, x + tw + pad * 2, y + th + pad * 2],
        fill=(0, 0, 0, 160),
    )
    draw.text((x + pad, y + pad), text, font=font, fill=(255, 255, 255, 255))
