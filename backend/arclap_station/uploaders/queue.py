"""Persistent retry queue for uploads with exponential back-off + jitter."""

from __future__ import annotations

import logging
import random
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arclap_station.db import Database, get_db
from arclap_station.photos.store import PhotoStore
from arclap_station.photos.store import get_store as get_photo_store
from arclap_station.uploaders import UploadError
from arclap_station.uploaders.manager import DestinationManager, get_manager

log = logging.getLogger(__name__)

DEFAULT_WORKERS = 4
MAX_BACKOFF_SECONDS = 3600
JITTER_PCT = 0.3

# Circuit breaker: if every enabled destination has failed at least
# this many consecutive times, pause the queue for BREAKER_PAUSE_SEC
# instead of grinding through retries. Saves disk I/O + battery on a
# Pi that's lost its uplink, and lets the journal stay readable.
BREAKER_FAIL_THRESHOLD = 10
BREAKER_PAUSE_SEC = 300.0

# Destination kinds that actually store the photo file. MQTT publishes
# metadata only (and webhook may too), so a "success" from those must
# NOT trigger a keep_local delete — that would lose the only copy.
_FILE_STORING_KINDS = {"ftp", "sftp", "s3", "local"}


@dataclass
class QueueItem:
    id: int
    photo_id: int
    dest_id: str
    state: str
    attempts: int
    next_at: str
    last_error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "photo_id": self.photo_id,
            "dest_id": self.dest_id,
            "state": self.state,
            "attempts": self.attempts,
            "next_at": self.next_at,
            "last_error": self.last_error,
        }


def _row(r: Any) -> QueueItem:
    return QueueItem(
        id=int(r["id"]),
        photo_id=int(r["photo_id"]),
        dest_id=str(r["dest_id"]),
        state=str(r["state"]),
        attempts=int(r["attempts"]),
        next_at=str(r["next_at"]),
        last_error=r["last_error"],
    )


def _backoff_seconds(attempts: int) -> float:
    base = min(MAX_BACKOFF_SECONDS, 2**attempts)
    jitter = base * JITTER_PCT
    return float(base + random.uniform(-jitter, jitter))


def _photo_key(photo_path: Path) -> str:
    """Convert /media/sdcard/photos/2026/05/19/ph_001.jpg → 2026/05/19/ph_001.jpg."""
    parts = photo_path.parts
    if len(parts) >= 4:
        return "/".join(parts[-4:])
    return photo_path.name


class UploadQueue:
    def __init__(
        self,
        db: Database | None = None,
        photo_store: PhotoStore | None = None,
        destinations: DestinationManager | None = None,
    ) -> None:
        self._db = db or get_db()
        self._photos = photo_store or get_photo_store()
        self._destinations = destinations or get_manager()
        self._stop_event = threading.Event()
        self._workers: list[threading.Thread] = []
        self._wakeup = threading.Event()

    # ----- public API ---------------------------------------------------

    def enqueue(self, photo_id: int, dest_ids: Iterable[str]) -> list[int]:
        ids: list[int] = []
        with self._db.tx() as conn:
            for dest_id in dest_ids:
                cur = conn.execute(
                    """
                    INSERT INTO upload_queue(photo_id, dest_id, state, attempts, next_at)
                    VALUES(?, ?, 'pending', 0, datetime('now'))
                    RETURNING id
                    """,
                    (photo_id, dest_id),
                )
                row = cur.fetchone()
                if row:
                    ids.append(int(row[0]))
        self._wakeup.set()
        return ids

    def list(self, state: str | None = None, limit: int = 200) -> list[QueueItem]:
        sql = "SELECT * FROM upload_queue"
        params: list[Any] = []
        if state:
            sql += " WHERE state = ?"
            params.append(state)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row(r) for r in rows]

    def stats(self) -> dict[str, int]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT state, COUNT(*) AS c FROM upload_queue GROUP BY state"
            ).fetchall()
        return {str(r["state"]): int(r["c"]) for r in rows}

    def pending_depth(self) -> int:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM upload_queue WHERE state IN ('pending','in_flight','failed')"
            ).fetchone()
        return int(row[0]) if row else 0

    def last_ok_at(self) -> str | None:
        """ISO timestamp of the most recent successful upload, or None."""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT MAX(updated_at) FROM upload_queue WHERE state='ok'"
            ).fetchone()
        return row[0] if row and row[0] else None

    def avg_upload_seconds(self, window: int = 50) -> float:
        """Average elapsed seconds (created_at → updated_at) over the last
        `window` successful uploads. 0.0 if no data yet."""
        with self._db.connect() as conn:
            rows = conn.execute(
                """
                SELECT (julianday(updated_at) - julianday(created_at)) * 86400.0 AS dt
                FROM upload_queue
                WHERE state='ok'
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, int(window)),),
            ).fetchall()
        if not rows:
            return 0.0
        vals = [float(r[0]) for r in rows if r[0] is not None and r[0] > 0]
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    # ----- worker loop --------------------------------------------------

    def start(self, n_workers: int = DEFAULT_WORKERS) -> None:
        self.recover_in_flight()
        for i in range(n_workers):
            t = threading.Thread(target=self._worker_loop, name=f"uploadq-{i}", daemon=True)
            t.start()
            self._workers.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._wakeup.set()
        for t in self._workers:
            t.join(timeout=timeout)
        self._workers.clear()

    def recover_in_flight(self) -> int:
        """Reset rows stranded in 'in_flight' by a crash/restart back to
        'pending' so they get retried.

        `_claim()` flips a row to 'in_flight' and only ever re-selects
        state IN ('pending','failed') — so a process restart mid-upload
        (which the watchdogs do on purpose) orphans that row forever, and
        retention later deletes the never-uploaded original. Called at
        queue start so every boot self-heals stranded uploads.
        """
        with self._db.tx() as conn:
            cur = conn.execute(
                "UPDATE upload_queue SET state='pending', next_at=datetime('now'), "
                "updated_at=datetime('now') WHERE state='in_flight'"
            )
        n = cur.rowcount
        if n:
            log.info("recovered %d stranded in_flight upload(s) after restart", n)
        return n

    def drain_once(self) -> int:
        """Process all currently-due items synchronously. Returns count processed."""
        processed = 0
        while True:
            item = self._claim()
            if item is None:
                return processed
            self._handle(item)
            processed += 1

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            # Circuit breaker — if every destination is failing, pause
            # so we don't burn CPU + the destination's rate-limit
            # budget retrying every 30s.
            wait = self._breaker_pause_remaining()
            if wait > 0:
                if self._wakeup.wait(timeout=min(wait, 10.0)):
                    self._wakeup.clear()
                continue
            item = self._claim()
            if item is None:
                self._wakeup.wait(timeout=2.0)
                self._wakeup.clear()
                continue
            self._handle(item)

    def _breaker_pause_remaining(self) -> float:
        """Seconds left to pause, or 0 if the breaker is closed.

        Open the breaker when EVERY enabled destination has been
        failing for ≥ BREAKER_FAIL_THRESHOLD recent attempts AND the
        most recent failure on any of them is within
        BREAKER_PAUSE_SEC. Close it as soon as one destination logs
        a fresh success.
        """
        dests = [d for d in self._destinations.list() if d.enabled]
        if not dests:
            return 0.0
        # Use last_error vs last_ok_at as the simple gate; if any
        # destination has a fresher OK than its last error, breaker
        # is closed.
        all_failing = True
        latest_err_ts: float | None = None
        from datetime import datetime as _dt, UTC as _UTC  # noqa: PLC0415

        for d in dests:
            if not d.last_error:
                all_failing = False
                break
            err_time = None
            ok_time = None
            try:
                if d.last_error:
                    # destination table doesn't carry an error-time
                    # column directly; use updated_at semantics via
                    # the queue's last in_flight row for this dest.
                    pass
                if d.last_ok_at:
                    ok_time = _dt.fromisoformat(d.last_ok_at.replace(" ", "T"))
                    if ok_time.tzinfo is None:
                        ok_time = ok_time.replace(tzinfo=_UTC)
            except (ValueError, AttributeError):
                pass
            # Count consecutive failures via the queue.
            with self._db.connect() as conn:
                cnt_row = conn.execute(
                    "SELECT COUNT(*) FROM upload_queue "
                    "WHERE dest_id=? AND state='failed' "
                    "  AND updated_at > datetime('now', '-1 hour')",
                    (d.id,),
                ).fetchone()
            fails = int(cnt_row[0]) if cnt_row else 0
            if fails < BREAKER_FAIL_THRESHOLD:
                all_failing = False
                break
        if not all_failing:
            return 0.0
        # Use the oldest "still pending" item's next_at as the breaker
        # release: if all retries are at least BREAKER_PAUSE_SEC out,
        # there's nothing to do anyway.
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT MIN(next_at) FROM upload_queue WHERE state IN ('pending','failed')"
            ).fetchone()
        if row and row[0]:
            try:
                ts = _dt.fromisoformat(str(row[0]).replace(" ", "T"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=_UTC)
                wait = (ts - _dt.now(_UTC)).total_seconds()
                return max(0.0, min(BREAKER_PAUSE_SEC, wait))
            except (ValueError, AttributeError):
                pass
        return BREAKER_PAUSE_SEC

    def _claim(self) -> QueueItem | None:
        with self._db.tx() as conn:
            row = conn.execute(
                """
                SELECT * FROM upload_queue
                WHERE state IN ('pending','failed')
                  AND next_at <= datetime('now')
                ORDER BY next_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE upload_queue SET state='in_flight', updated_at=datetime('now') "
                "WHERE id=?",
                (row["id"],),
            )
        return _row(row)

    def _handle(self, item: QueueItem) -> None:
        photo = self._photos.get(item.photo_id)
        dest = self._destinations.get(item.dest_id)
        if photo is None or dest is None or not dest.enabled:
            self._mark_failed(item, "photo or destination missing/disabled", permanent=True)
            return
        try:
            uploader = self._destinations.build_uploader(item.dest_id)
            key = _photo_key(Path(photo.path))
            uploader.upload(Path(photo.path), key)
            uploader.close()
        except UploadError as exc:
            self._mark_retry(item, str(exc))
            self._destinations.mark_error(dest.id, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("upload worker raised: %s", exc)
            self._mark_retry(item, str(exc))
            self._destinations.mark_error(dest.id, str(exc))
            return
        self._mark_ok(item)
        self._destinations.mark_ok(dest.id)

    def _mark_ok(self, item: QueueItem) -> None:
        all_done = False
        photo_path: str | None = None
        schedule_id: str | None = None
        with self._db.tx() as conn:
            conn.execute(
                "UPDATE upload_queue SET state='ok', updated_at=datetime('now') WHERE id=?",
                (item.id,),
            )
            row = conn.execute(
                "SELECT COUNT(*) FROM upload_queue WHERE photo_id=? AND state != 'ok'",
                (item.photo_id,),
            ).fetchone()
            if int(row[0]) == 0:
                conn.execute(
                    "UPDATE photos SET upload_state='done' WHERE id=?", (item.photo_id,)
                )
                # Fetch the photo's source path + originating schedule
                # id so the keep-local hook below can decide whether
                # to clean up. We do this inside the tx so the read
                # is consistent with the state=done we just wrote.
                prow = conn.execute(
                    "SELECT path, job_id FROM photos WHERE id=?",
                    (item.photo_id,),
                ).fetchone()
                if prow is not None:
                    photo_path = prow[0]
                    schedule_id = prow[1]
                all_done = True

        # Outside the tx — file deletion + audit are best-effort and
        # mustn't hold the DB lock. Only relevant when EVERY destination
        # for this photo has finished successfully.
        if all_done and schedule_id and photo_path:
            self._maybe_delete_local(photo_path, schedule_id, item.photo_id)

    def _maybe_delete_local(
        self, photo_path: str, schedule_id: str, photo_id: int
    ) -> None:
        """Delete the local SD-card file if the schedule says don't keep it.

        Only triggered when a photo originated from a schedule
        (job_id != NULL) AND every destination has uploaded
        successfully. Manual captures (job_id=None) always keep their
        local copy — there's no operator-set policy to drive a delete
        on those, and Gallery thumbnails would break.
        """
        try:
            with self._db.connect() as conn:
                row = conn.execute(
                    "SELECT conditions FROM schedules WHERE id=?",
                    (schedule_id,),
                ).fetchone()
            if not row or not row[0]:
                return
            import json as _json  # noqa: PLC0415
            try:
                cond = _json.loads(row[0]) or {}
            except (ValueError, TypeError):
                return
            if not isinstance(cond, dict) or cond.get("keep_local", True):
                # Default behaviour: keep the local copy. Only delete
                # if the schedule explicitly opted out.
                return
            # Only delete the local file if at least one destination that
            # ACTUALLY STORES THE FILE has it. MQTT publishes metadata
            # only (and webhook may too); deleting the local copy after a
            # metadata-only "success" would lose the photo entirely.
            with self._db.connect() as conn:
                dest_rows = conn.execute(
                    "SELECT DISTINCT d.type FROM upload_queue q "
                    "JOIN destinations d ON d.id = q.dest_id "
                    "WHERE q.photo_id = ? AND q.state = 'ok'",
                    (photo_id,),
                ).fetchall()
            kinds = {str(r[0]) for r in dest_rows}
            if not (kinds & _FILE_STORING_KINDS):
                log.warning(
                    "keep_local=False for photo %d but no file-storing destination "
                    "has it (only %s) — keeping local copy to avoid data loss",
                    photo_id, ", ".join(sorted(kinds)) or "none",
                )
                return
            from pathlib import Path as _P  # noqa: PLC0415
            p = _P(photo_path)
            if p.exists():
                p.unlink()
                log.info(
                    "schedule keep_local=False: removed local file %s (photo %d)",
                    photo_path,
                    photo_id,
                )
                try:
                    from arclap_station.audit import emit as _audit  # noqa: PLC0415
                    _audit(
                        "system",
                        "photo.local_removed",
                        {
                            "photo_id": photo_id,
                            "schedule_id": schedule_id,
                            "reason": "keep_local=False after successful upload",
                        },
                    )
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            log.warning("keep_local delete failed for %s: %s", photo_path, exc)

    def _mark_retry(self, item: QueueItem, err: str) -> None:
        attempts = item.attempts + 1
        if attempts >= 12:
            self._mark_failed(item, err, permanent=True)
            return
        # Store next_at in SQLite's own datetime format via datetime('now',
        # '+N seconds'). Previously we wrote isoformat() ("...T...+00:00")
        # but _claim() compares `next_at <= datetime('now')` which yields
        # space-separated, tz-less text — a lexicographic mismatch ('T' >
        # ' ') that made every retry undue until the next UTC midnight.
        delay = max(1, int(round(_backoff_seconds(attempts))))
        with self._db.tx() as conn:
            conn.execute(
                """
                UPDATE upload_queue
                SET state='failed', attempts=?, next_at=datetime('now', ?),
                    last_error=?, updated_at=datetime('now')
                WHERE id=?
                """,
                (attempts, f"+{delay} seconds", err[:1024], item.id),
            )

    def _mark_failed(self, item: QueueItem, err: str, *, permanent: bool) -> None:
        state = "failed_permanent" if permanent else "failed"
        with self._db.tx() as conn:
            conn.execute(
                "UPDATE upload_queue SET state=?, last_error=?, updated_at=datetime('now') "
                "WHERE id=?",
                (state, err[:1024], item.id),
            )
        # Audit event when retries are exhausted (permanent=True). The
        # cockpit's Activity feed pulls these and the operator finally
        # sees why their photos aren't landing — before this emit,
        # `last_error` on the destination card was the only signal,
        # and that gets cleared by any subsequent success so the
        # evidence vanished. CLAUDE.md §12.10 requires we log this.
        if permanent:
            try:
                from arclap_station.audit import emit as _audit  # noqa: PLC0415
                _audit(
                    "system",
                    "upload.failed_permanent",
                    {
                        "queue_item_id": item.id,
                        "photo_id": item.photo_id,
                        "dest_id": item.dest_id,
                        "attempts": item.attempts + 1,
                        "last_error": err[:512],
                    },
                )
            except Exception:  # noqa: BLE001
                # Audit must not block recovery — if the audit log is
                # itself jammed (rare; FS full) we still want the
                # queue worker to keep moving on to the next item.
                pass


_queue: UploadQueue | None = None


def get_queue() -> UploadQueue:
    global _queue
    if _queue is None:
        _queue = UploadQueue()
    return _queue


def reset_queue_singleton() -> None:
    global _queue
    if _queue is not None:
        _queue.stop()
    _queue = None
