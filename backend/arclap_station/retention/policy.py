"""Disk-retention policy.

Tiered keep/delete logic so the SD card on a 2-year construction-site
deployment never fills:

  HOT     (0–7 days)   — always kept (regardless of upload state)
  WARM    (7–30 days)  — kept if uploaded OR starred
  COLD    (30–90 days) — kept only if starred
  ARCHIVE (90+ days)   — kept only if starred

Triggered:
- Daily by the arclap-retention.timer (03:00 local).
- On-demand via `arclap-station retention-sweep`.

When disk usage >= EMERGENCY_PCT we ignore the hot tier and delete
unstarred from oldest until we're under TARGET_PCT — the station must
keep capturing rather than refuse new shots because the SD card is full.

Every sweep writes a structured audit event for forensics.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arclap_station.audit import emit as audit_emit
from arclap_station.config import get_settings
from arclap_station.db import get_db
from arclap_station.photos.store import get_store

log = logging.getLogger(__name__)

# Tier age bounds (days).
HOT_DAYS = 7
WARM_DAYS = 30
COLD_DAYS = 90

# Disk thresholds (0..1 fractions).
TRIGGER_PCT = 0.75      # start sweeping above this
TARGET_PCT = 0.65       # sweep until below this
EMERGENCY_PCT = 0.95    # emergency: ignore hot tier


@dataclass
class SweepReport:
    started_at: str
    finished_at: str
    disk_used_before_pct: float
    disk_used_after_pct: float
    photos_deleted: int
    bytes_freed: int
    emergency_mode: bool
    triggered: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "disk_used_before_pct": round(self.disk_used_before_pct, 2),
            "disk_used_after_pct": round(self.disk_used_after_pct, 2),
            "photos_deleted": self.photos_deleted,
            "bytes_freed": self.bytes_freed,
            "emergency_mode": self.emergency_mode,
            "triggered": self.triggered,
        }


def disk_usage_pct(path: Path) -> float:
    """Return 0..100 percentage of disk used at `path`."""
    if not path.exists():
        return 0.0
    usage = shutil.disk_usage(path)
    return (usage.used / usage.total) * 100 if usage.total > 0 else 0.0


def _photo_tier(captured_at: datetime, now: datetime) -> str:
    age_days = (now - captured_at).total_seconds() / 86400
    if age_days < HOT_DAYS:
        return "hot"
    if age_days < WARM_DAYS:
        return "warm"
    if age_days < COLD_DAYS:
        return "cold"
    return "archive"


def _should_keep(tier: str, uploaded: bool, starred: bool, emergency: bool) -> bool:
    if starred:
        return True
    if emergency:
        return False  # only starred survives in emergency
    if tier == "hot":
        return True
    if tier == "warm":
        return uploaded
    # cold + archive: only starred (already returned above)
    return False


def sweep(force: bool = False) -> SweepReport:
    settings = get_settings()
    photos_root = settings.paths.photos
    now = datetime.now(UTC)
    before_pct = disk_usage_pct(photos_root)

    triggered = force or before_pct >= TRIGGER_PCT * 100
    if not triggered:
        log.info(
            "disk at %.1f%% — under trigger (%.0f%%), sweep skipped",
            before_pct,
            TRIGGER_PCT * 100,
        )
        finished = datetime.now(UTC)
        return SweepReport(
            started_at=now.isoformat(),
            finished_at=finished.isoformat(),
            disk_used_before_pct=before_pct,
            disk_used_after_pct=before_pct,
            photos_deleted=0,
            bytes_freed=0,
            emergency_mode=False,
            triggered=False,
        )

    emergency = before_pct >= EMERGENCY_PCT * 100
    deleted = 0
    bytes_freed = 0

    db = get_db()
    store = get_store()

    # Pull candidates oldest-first so we delete oldest first.
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, path, captured_at, size_bytes, upload_state, "
            "       COALESCE(starred, 0) AS starred "
            "FROM photos ORDER BY captured_at ASC"
        ).fetchall()

    for r in rows:
        captured = _parse_iso(r["captured_at"], fallback=now)
        tier = _photo_tier(captured, now)
        uploaded = (r["upload_state"] == "done")
        starred = bool(r["starred"])

        if _should_keep(tier, uploaded, starred, emergency):
            continue

        size = int(r["size_bytes"] or 0)
        if store.delete(int(r["id"])):
            deleted += 1
            bytes_freed += size

        # If non-emergency, stop as soon as we hit the target threshold.
        if not emergency and deleted % 10 == 0:
            now_pct = disk_usage_pct(photos_root)
            if now_pct <= TARGET_PCT * 100:
                break

    after_pct = disk_usage_pct(photos_root)
    finished = datetime.now(UTC)

    report = SweepReport(
        started_at=now.isoformat(),
        finished_at=finished.isoformat(),
        disk_used_before_pct=before_pct,
        disk_used_after_pct=after_pct,
        photos_deleted=deleted,
        bytes_freed=bytes_freed,
        emergency_mode=emergency,
        triggered=True,
    )
    try:
        audit_emit("system", "retention.sweep", report.to_dict())
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_emit failed: %s", exc)
    return report


def _parse_iso(s: str, fallback: datetime) -> datetime:
    try:
        ts = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts
    except (ValueError, AttributeError):
        return fallback


def run() -> int:
    """CLI entrypoint."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    try:
        report = sweep()
        log.info(
            "retention sweep: deleted=%d freed=%.1f MB disk %.1f%% → %.1f%% emergency=%s triggered=%s",
            report.photos_deleted,
            report.bytes_freed / 1_000_000,
            report.disk_used_before_pct,
            report.disk_used_after_pct,
            report.emergency_mode,
            report.triggered,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        log.exception("retention sweep crashed: %s", exc)
        return 1
