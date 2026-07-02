"""Pre-rendered timelapse videos — the strategic retention asset (§12.13).

After the retention sweep we have the day's photos on the SD card.
This module turns them into an MP4 the customer can scroll on the
cockpit / download / share without needing to keep all the raw frames
forever.

Pipeline:
  1. Collect photos captured during the target window (default: last
     24 h) into a ffconcat list.
  2. Pipe through ffmpeg → libx264 yuv420p, 24 fps, 1080p down-scaled
     from the camera's 6720×4480 native (libx264 won't accept odd
     dimensions so we pad/crop to mod-2).
  3. Write to /var/lib/arclap/timelapses/<period>-<start>.mp4.
  4. Register in a new `timelapses` table so the cockpit can list them.

The render runs as a systemd timer (arclap-timelapse.timer) at 03:30 —
between the retention sweep (03:00) and the backup (04:00), so:
  - retention keeps the day's photos warm
  - timelapse uses them for input
  - backup snapshots the resulting `timelapses` table

Bounded resource usage:
  - ffmpeg with -threads 2 and -tune zerolatency keeps CPU bounded so
    we don't compete with capture / upload during render.
  - Output cap: 30 sec @ 24fps = 720 frames max per day; longer days
    are sub-sampled.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from arclap_station.audit import emit as audit_emit
from arclap_station.config import get_settings
from arclap_station.db import get_db

log = logging.getLogger(__name__)

OUTPUT_FPS = 24
MAX_FRAMES = 720          # ≈ 30 sec @ 24 fps
TARGET_HEIGHT = 1080      # 1080p output
LIBX264_PRESET = "veryfast"
LIBX264_CRF = 23
RETAIN_DAYS = 30


@dataclass
class TimelapseRun:
    period: str
    started_at: str
    finished_at: str
    photos_in: int
    duration_sec: float
    output_path: str
    size_bytes: int


def render_window(start: datetime, end: datetime, *, period: str) -> dict[str, Any]:
    """Render a single timelapse covering [start, end).

    Returns a dict suitable for JSON return / audit. `period` is a
    label like "day" / "week" stored in the timelapses row.
    """
    if shutil.which("ffmpeg") is None:
        return {"ok": False, "reason": "ffmpeg not available"}

    db = get_db()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, path, captured_at FROM photos "
            "WHERE captured_at >= ? AND captured_at < ? "
            "ORDER BY captured_at ASC",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    if len(rows) < 2:
        return {"ok": False, "reason": "not_enough_photos", "count": len(rows)}

    # Sub-sample if we're over MAX_FRAMES — pick evenly spaced photos.
    if len(rows) > MAX_FRAMES:
        step = len(rows) / MAX_FRAMES
        rows = [rows[int(i * step)] for i in range(MAX_FRAMES)]

    out_dir = get_settings().paths.var / "timelapses"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = start.strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"{period}-{stamp}.mp4"

    started_at = datetime.now(UTC)
    with tempfile.TemporaryDirectory() as td:
        # ffconcat input list — quote paths so spaces work.
        concat_path = Path(td) / "concat.txt"
        with concat_path.open("w") as fh:
            fh.write("ffconcat version 1.0\n")
            for r in rows:
                p = Path(r["path"]).resolve()
                if not p.exists():
                    continue
                fh.write(f"file '{p.as_posix()}'\n")
                fh.write(f"duration {1.0 / OUTPUT_FPS}\n")
            # ffmpeg quirk — last file needs to be repeated without
            # duration for correct end-of-stream.
            if rows:
                last = Path(rows[-1]["path"]).resolve()
                fh.write(f"file '{last.as_posix()}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_path),
            "-vf", f"scale=-2:{TARGET_HEIGHT},pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-r", str(OUTPUT_FPS),
            "-c:v", "libx264",
            "-preset", LIBX264_PRESET,
            "-crf", str(LIBX264_CRF),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-threads", "2",
            str(out_path),
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        except subprocess.SubprocessError as exc:
            log.exception("ffmpeg failed")
            return {"ok": False, "reason": "ffmpeg_exception", "error": str(exc)}
        if r.returncode != 0:
            log.error("ffmpeg exit=%s stderr=%s", r.returncode, r.stderr[-2000:])
            return {"ok": False, "reason": "ffmpeg_error", "stderr": r.stderr[-200:]}

    if not out_path.exists():
        return {"ok": False, "reason": "no_output"}

    finished_at = datetime.now(UTC)
    duration_sec = (finished_at - started_at).total_seconds()
    size_bytes = out_path.stat().st_size
    photos_in = len(rows)

    # Register in DB.
    with db.tx() as conn:
        conn.execute(
            """
            INSERT INTO timelapses(period, start_at, end_at, photos_in,
                                   render_ms, path, size_bytes)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (period, start.isoformat(), end.isoformat(), photos_in,
             int(duration_sec * 1000), str(out_path), size_bytes),
        )
    _prune_old_renders()
    audit_emit("system", "timelapse.rendered", {
        "period": period,
        "photos_in": photos_in,
        "duration_sec": round(duration_sec, 1),
        "size_bytes": size_bytes,
        "path": str(out_path),
    })
    return {
        "ok": True,
        "period": period,
        "photos_in": photos_in,
        "duration_sec": round(duration_sec, 1),
        "output_path": str(out_path),
        "size_bytes": size_bytes,
    }


def _prune_old_renders() -> None:
    """Drop timelapse rows + files older than RETAIN_DAYS."""
    cutoff = datetime.now(UTC) - timedelta(days=RETAIN_DAYS)
    db = get_db()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, path FROM timelapses WHERE created_at < ?",
            (cutoff.isoformat(),),
        ).fetchall()
    if not rows:
        return
    with db.tx() as conn:
        for r in rows:
            try:
                Path(r["path"]).unlink()
            except OSError:
                pass
            conn.execute("DELETE FROM timelapses WHERE id=?", (r["id"],))


def run_daily() -> int:
    """CLI: render the last 24 h as a timelapse."""
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%S")
    end = datetime.now(UTC).replace(microsecond=0)
    start = end - timedelta(days=1)
    res = render_window(start, end, period="day")
    log.info("timelapse.daily: %s", res)
    return 0 if res.get("ok") else 1
