"""Upload queue — crash recovery, retry-timestamp format, keep_local safety.

Regression guards for:
- P0: retry next_at written as ISO-T but compared against SQLite
  space-format now() → retries stalled until UTC midnight.
- P1: in_flight rows stranded forever by a restart mid-upload.
- P1: keep_local=False deleted the local file even when only a
  metadata-only (MQTT) destination "succeeded" → total photo loss.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from arclap_station.db import get_db
from arclap_station.photos.store import get_store
from arclap_station.uploaders.manager import get_manager
from arclap_station.uploaders.queue import QueueItem, UploadQueue


def test_recover_in_flight_resets_to_pending(fresh_db: Any, tmp_path: Path) -> None:
    d = get_manager().create("f", "ftp", {"host": "h"}, enabled=True)
    p = get_store().register(tmp_path / "a.jpg", size_bytes=10)
    q = UploadQueue()
    (qid,) = q.enqueue(p.id, [d.id])
    with get_db().tx() as conn:
        conn.execute("UPDATE upload_queue SET state='in_flight' WHERE id=?", (qid,))
    assert q.recover_in_flight() == 1
    with get_db().connect() as conn:
        st = conn.execute("SELECT state FROM upload_queue WHERE id=?", (qid,)).fetchone()[0]
    assert st == "pending"


def test_mark_retry_writes_sqlite_datetime_format(fresh_db: Any, tmp_path: Path) -> None:
    d = get_manager().create("f", "ftp", {"host": "h"}, enabled=True)
    p = get_store().register(tmp_path / "a.jpg", size_bytes=10)
    q = UploadQueue()
    (qid,) = q.enqueue(p.id, [d.id])
    item = QueueItem(id=qid, photo_id=p.id, dest_id=d.id, state="pending",
                     attempts=0, next_at="", last_error=None)
    q._mark_retry(item, "boom")  # noqa: SLF001
    with get_db().connect() as conn:
        row = conn.execute("SELECT state, next_at FROM upload_queue WHERE id=?", (qid,)).fetchone()
    assert row[0] == "failed"
    # Space-format, no 'T'/'+00:00' — so `next_at <= datetime('now')` in
    # _claim() compares correctly instead of stalling until UTC midnight.
    assert "T" not in row[1] and "+" not in row[1], f"bad next_at format: {row[1]!r}"


def _schedule_with_dest(dest_id: str, keep_local: bool) -> str:
    with get_db().tx() as conn:
        conn.execute(
            "INSERT INTO schedules(id, name, interval_min, from_time, to_time, "
            "days_csv, enabled, dest_filter, conditions) "
            "VALUES('s', 'S', 10, '00:00', '23:59', 'mon,tue,wed,thu,fri,sat,sun', 1, ?, ?)",
            (dest_id, json.dumps({"keep_local": keep_local})),
        )
    return "s"


def test_keep_local_kept_when_only_mqtt_succeeded(fresh_db: Any, tmp_path: Path) -> None:
    d = get_manager().create("m", "mqtt", {"broker": "mqtt://x:1883", "topic": "t"}, enabled=True)
    _schedule_with_dest(d.id, keep_local=False)
    f = tmp_path / "p.jpg"
    f.write_bytes(b"x" * 100)
    p = get_store().register(f, size_bytes=100, job_id="s")
    with get_db().tx() as conn:
        conn.execute(
            "INSERT INTO upload_queue(photo_id, dest_id, state, attempts, next_at) "
            "VALUES(?, ?, 'ok', 0, datetime('now'))",
            (p.id, d.id),
        )
    UploadQueue()._maybe_delete_local(str(f), "s", p.id)  # noqa: SLF001
    assert f.exists(), "local file must survive when only a metadata-only (mqtt) dest succeeded"


def test_keep_local_deleted_when_file_storing_dest_succeeded(fresh_db: Any, tmp_path: Path) -> None:
    d = get_manager().create("f", "ftp", {"host": "h", "user": "u", "password": "p"}, enabled=True)
    _schedule_with_dest(d.id, keep_local=False)
    f = tmp_path / "p.jpg"
    f.write_bytes(b"x" * 100)
    p = get_store().register(f, size_bytes=100, job_id="s")
    with get_db().tx() as conn:
        conn.execute(
            "INSERT INTO upload_queue(photo_id, dest_id, state, attempts, next_at) "
            "VALUES(?, ?, 'ok', 0, datetime('now'))",
            (p.id, d.id),
        )
    UploadQueue()._maybe_delete_local(str(f), "s", p.id)  # noqa: SLF001
    assert not f.exists(), "local file should be removed after a real (ftp) store with keep_local=False"
