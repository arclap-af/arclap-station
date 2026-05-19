"""Persistent retry queue for uploads with exponential back-off + jitter."""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
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

    # ----- worker loop --------------------------------------------------

    def start(self, n_workers: int = DEFAULT_WORKERS) -> None:
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
            item = self._claim()
            if item is None:
                self._wakeup.wait(timeout=2.0)
                self._wakeup.clear()
                continue
            self._handle(item)

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

    def _mark_retry(self, item: QueueItem, err: str) -> None:
        attempts = item.attempts + 1
        if attempts >= 12:
            self._mark_failed(item, err, permanent=True)
            return
        next_at = datetime.fromtimestamp(time.time() + _backoff_seconds(attempts), tz=UTC)
        with self._db.tx() as conn:
            conn.execute(
                """
                UPDATE upload_queue
                SET state='failed', attempts=?, next_at=?, last_error=?,
                    updated_at=datetime('now')
                WHERE id=?
                """,
                (attempts, next_at.isoformat(timespec="seconds"), err[:1024], item.id),
            )

    def _mark_failed(self, item: QueueItem, err: str, *, permanent: bool) -> None:
        state = "failed_permanent" if permanent else "failed"
        with self._db.tx() as conn:
            conn.execute(
                "UPDATE upload_queue SET state=?, last_error=?, updated_at=datetime('now') "
                "WHERE id=?",
                (state, err[:1024], item.id),
            )


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
