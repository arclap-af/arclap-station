"""Perceptual-hash deduplication for the capture queue.

A construction site at 4am Sunday looks identical to 4:05am Sunday;
storing every one of those is a waste of SD card + bandwidth + S3.
We compute a 64-bit perceptual hash (dHash variant) for every photo
and drop the new one if it's within HAMMING_THRESHOLD of the previous
photo's hash AND captured within DUP_WINDOW_SEC.

Why dHash over pHash or aHash:
  - dHash is 64-bit, fast to compute on Pi (resize 8×9, gradient).
  - Robust to slight exposure / colour shifts (which a tripod
    timelapse will have between every frame as the sun moves).
  - More discriminating than aHash on flat-toned frames.

Disabled by default — opt in via station_config.dedup_threshold (None
= off). Construction sites with very static scenes set it to 4 (very
aggressive); event coverage sites set it to 0.

The pHash column is added by migration v3 below. We backfill on first
access so existing photos benefit from "what's the latest hash?"
queries.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Default threshold (Hamming distance) treated as "duplicate". 0 =
# strictly identical bits; 4–6 = "looks the same to a human"; 10+ =
# "loosely related". Most construction-site stations use 4-6.
DEFAULT_THRESHOLD = 4
# Two captures further apart than this are never considered duplicates
# even if pixels are identical — different time = different evidence.
DUP_WINDOW_SEC = 600.0


def compute_dhash(path: Path) -> int | None:
    """64-bit difference hash of a JPEG.

    Returns None if Pillow is missing or the image won't decode.
    The value fits in a Python int but we store it as text in SQLite
    for portability.
    """
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return None
    try:
        with Image.open(str(path)) as img:
            small = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            pixels = list(small.getdata())
    except Exception as exc:  # noqa: BLE001
        log.debug("dhash decode failed on %s: %s", path, exc)
        return None
    bits = 0
    for row in range(8):
        for col in range(8):
            left = pixels[row * 9 + col]
            right = pixels[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming(a: int, b: int) -> int:
    """Population count of the XOR — Hamming distance between two 64-bit hashes."""
    return bin(a ^ b).count("1")


def is_near_duplicate(new_hash: int, prev_hash: int, threshold: int | None = None) -> bool:
    """True if `new_hash` should be treated as a duplicate of `prev_hash`."""
    if threshold is None:
        threshold = DEFAULT_THRESHOLD
    return hamming(new_hash, prev_hash) <= threshold


def latest_hash_for_job(job_id: str | None) -> tuple[int, str] | None:
    """Return (hash, captured_at) of the most recent photo in this job, or None.

    We restrict by job (schedule) so manually-triggered captures don't
    suppress a scheduled one and vice-versa.
    """
    from arclap_station.db import get_db  # noqa: PLC0415

    with get_db().connect() as conn:
        if job_id is None:
            row = conn.execute(
                "SELECT phash, captured_at FROM photos "
                "WHERE phash IS NOT NULL AND job_id IS NULL "
                "ORDER BY captured_at DESC LIMIT 1"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT phash, captured_at FROM photos "
                "WHERE phash IS NOT NULL AND job_id = ? "
                "ORDER BY captured_at DESC LIMIT 1",
                (job_id,),
            ).fetchone()
    if row is None or row["phash"] is None:
        return None
    try:
        return int(row["phash"]), str(row["captured_at"])
    except (ValueError, TypeError):
        return None


def maybe_drop_duplicate(path: Path, job_id: str | None, threshold: int) -> bool:
    """If the photo is a near-duplicate of the previous one in the same job,
    delete it and return True. Otherwise return False (caller proceeds).

    Caller is responsible for storing the resulting hash (see store_hash)
    once they've decided to keep the photo.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    new_hash = compute_dhash(path)
    if new_hash is None:
        return False
    prev = latest_hash_for_job(job_id)
    if prev is None:
        return False
    prev_hash, prev_ts = prev
    # Time gate — never drop a frame more than DUP_WINDOW_SEC after the
    # previous one. Different time = different evidence.
    try:
        prev_dt = datetime.fromisoformat(prev_ts.replace("Z", "+00:00").replace(" ", "T"))
        if prev_dt.tzinfo is None:
            prev_dt = prev_dt.replace(tzinfo=UTC)
        if (datetime.now(UTC) - prev_dt).total_seconds() > DUP_WINDOW_SEC:
            return False
    except (ValueError, AttributeError):
        pass
    if not is_near_duplicate(new_hash, prev_hash, threshold):
        return False
    try:
        path.unlink()
    except OSError:
        pass
    return True


def store_hash(photo_id: int, hash_value: int) -> None:
    """Record the perceptual hash on a photo row."""
    from arclap_station.db import get_db  # noqa: PLC0415

    with get_db().tx() as conn:
        conn.execute(
            "UPDATE photos SET phash = ? WHERE id = ?",
            (str(hash_value), int(photo_id)),
        )
