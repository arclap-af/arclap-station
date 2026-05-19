"""Daily database backup + weekly integrity check.

Why this exists:
- state.db holds everything that ties photos to schedules, destinations,
  upload state, audit chain, and acceptance results. SD card corruption
  losing it = blind to the deployment's history.
- A live snapshot using `sqlite3 .backup` is the only safe way to copy
  a WAL-mode database; `cp` of state.db while the service is running
  yields a torn file.
- Compressed snapshots (~30% of raw size) so 7 days of retained
  backups fit in <10 MB on the SD card.
- Weekly PRAGMA integrity_check catches silent corruption from SD card
  wear-out months before it cascades into application-level errors.

Wired in via systemd timer (arclap-backup.timer, daily 04:00 local)
and CLI (`arclap-station backup` / `arclap-station db-integrity`).

Rotation: keep `RETAIN_DAYS` of compressed snapshots, prune the rest.
"""

from __future__ import annotations

import gzip
import logging
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from arclap_station.audit import emit as audit_emit
from arclap_station.config import get_settings

log = logging.getLogger(__name__)

RETAIN_DAYS = 7
BACKUP_SUBDIR = "backups"


def _backup_root() -> Path:
    root = get_settings().paths.var / BACKUP_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def _snapshot_name(now: datetime) -> str:
    return f"state-{now.strftime('%Y%m%d-%H%M%S')}.db.gz"


def take_snapshot() -> dict[str, Any]:
    """Take a compressed live snapshot of state.db.

    Uses SQLite's online backup API (page-by-page copy with a lock
    that doesn't block readers and only briefly blocks the writer).
    Output is gzipped to keep the SD card footprint small over a
    2-year deployment.
    """
    src_path = get_settings().paths.state_db
    if not src_path.exists():
        return {"ok": False, "reason": "no_source_db"}
    now = datetime.now(UTC)
    out_path = _backup_root() / _snapshot_name(now)
    # First: page-level copy to a temp .db file (online backup API).
    tmp_db = out_path.with_suffix(".db.tmp")
    try:
        src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
        dst = sqlite3.connect(tmp_db)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        # Then: gzip into final filename.
        with tmp_db.open("rb") as fin, gzip.open(out_path, "wb", compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout)
    finally:
        tmp_db.unlink(missing_ok=True)
    size_bytes = out_path.stat().st_size
    pruned = _rotate_old_snapshots()
    result = {
        "ok": True,
        "path": str(out_path),
        "size_bytes": size_bytes,
        "pruned": pruned,
        "retained_days": RETAIN_DAYS,
    }
    try:
        audit_emit("system", "db.backup", result)
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_emit('db.backup') failed: %s", exc)
    return result


def _rotate_old_snapshots() -> int:
    cutoff = datetime.now(UTC) - timedelta(days=RETAIN_DAYS)
    cutoff_ts = cutoff.timestamp()
    pruned = 0
    for p in _backup_root().glob("state-*.db.gz"):
        try:
            if p.stat().st_mtime < cutoff_ts:
                p.unlink()
                pruned += 1
        except OSError:
            continue
    return pruned


def integrity_check() -> dict[str, Any]:
    """PRAGMA integrity_check the live DB.

    Returns {ok, result}. `result == "ok"` is the happy path; any other
    value indicates corruption that needs an operator. Audit emit on
    both success and failure so the trail is preserved.
    """
    db_path = get_settings().paths.state_db
    if not db_path.exists():
        return {"ok": False, "reason": "no_db"}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            result_text = row[0] if row else "no_result"
        finally:
            conn.close()
    except sqlite3.Error as exc:
        log.exception("integrity_check failed to open db")
        try:
            audit_emit("system", "db.integrity_failed", {"error": str(exc)})
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "reason": "open_error", "error": str(exc)}
    ok = (result_text == "ok")
    try:
        audit_emit(
            "system",
            "db.integrity_ok" if ok else "db.integrity_failed",
            {"result": result_text},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_emit('db.integrity_*') failed: %s", exc)
    return {"ok": ok, "result": result_text}


def run_backup() -> int:
    """CLI entrypoint for `arclap-station backup`."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    try:
        result = take_snapshot()
        log.info("backup: %s", result)
        return 0 if result.get("ok") else 1
    except Exception as exc:  # noqa: BLE001
        log.exception("backup crashed: %s", exc)
        return 2


def run_integrity() -> int:
    """CLI entrypoint for `arclap-station db-integrity`."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    try:
        result = integrity_check()
        log.info("integrity_check: %s", result)
        return 0 if result.get("ok") else 1
    except Exception as exc:  # noqa: BLE001
        log.exception("integrity_check crashed: %s", exc)
        return 2
