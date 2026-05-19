"""APScheduler engine with SQLAlchemyJobStore.

Each schedule lives in the `schedules` table of state.db AND has a paired job
in scheduler.db so APScheduler can keep firing across reboots. The two are
linked by id.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from arclap_station.camera.adapter import get_adapter
from arclap_station.config import get_settings
from arclap_station.db import Database, get_db
from arclap_station.photos.store import PhotoStore, get_store
from arclap_station.scheduler.rules import list_destination_ids, should_skip
from arclap_station.uploaders.queue import UploadQueue, get_queue

log = logging.getLogger(__name__)


@dataclass
class Schedule:
    id: str
    name: str
    interval_min: int
    from_time: str
    to_time: str
    days_csv: str
    enabled: bool
    dest_filter: str | None
    conditions: str | None
    created_at: str
    updated_at: str

    @property
    def days(self) -> list[str]:
        return [d for d in self.days_csv.split(",") if d]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "interval_min": self.interval_min,
            "from_time": self.from_time,
            "to_time": self.to_time,
            "days": self.days,
            "enabled": self.enabled,
            "dest_filter": self.dest_filter,
            "conditions": self.conditions,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _row(r: Any) -> Schedule:
    return Schedule(
        id=str(r["id"]),
        name=str(r["name"]),
        interval_min=int(r["interval_min"]),
        from_time=str(r["from_time"]),
        to_time=str(r["to_time"]),
        days_csv=str(r["days_csv"]),
        enabled=bool(r["enabled"]),
        dest_filter=r["dest_filter"],
        conditions=r["conditions"],
        created_at=str(r["created_at"]),
        updated_at=str(r["updated_at"]),
    )


def fire_capture(schedule_id: str) -> dict[str, Any]:
    """Top-level job target — must be importable by APScheduler from a fresh process."""
    db = get_db()
    log.info("fire_capture schedule=%s", schedule_id)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM schedules WHERE id=?", (schedule_id,)
        ).fetchone()
    if row is None:
        log.warning("schedule %s no longer exists", schedule_id)
        return {"ok": False, "error": "no_such_schedule"}
    sched = _row(row)
    if not sched.enabled:
        return {"ok": False, "skipped": True, "reason": "disabled"}
    decision = should_skip(
        days_csv=sched.days_csv,
        from_time=sched.from_time,
        to_time=sched.to_time,
        dest_filter=sched.dest_filter,
        now=datetime.now(),
    )
    if decision.skip:
        log.info("skipping capture: %s", decision.reason)
        return {"ok": False, "skipped": True, "reason": decision.reason}

    adapter = get_adapter()
    info = adapter.detect()
    if not info.detected:
        return {"ok": False, "skipped": True, "reason": "no_camera"}

    photo_path = adapter.capture()
    store = get_store()
    record = store.register(photo_path, job_id=schedule_id)
    dest_ids = list_destination_ids(sched.dest_filter)
    if dest_ids:
        get_queue().enqueue(record.id, dest_ids)
    return {"ok": True, "photo_id": record.id, "destinations": len(dest_ids)}


class ScheduleEngine:
    def __init__(
        self,
        db: Database | None = None,
        photo_store: PhotoStore | None = None,
        upload_queue: UploadQueue | None = None,
        *,
        autostart: bool = False,
        timezone_name: str = "UTC",
    ) -> None:
        self._db = db or get_db()
        self._photos = photo_store or get_store()
        self._queue = upload_queue or get_queue()
        self._timezone_name = timezone_name
        self._lock = threading.RLock()
        scheduler_path = get_settings().paths.scheduler_db
        scheduler_path.parent.mkdir(parents=True, exist_ok=True)
        jobstore = SQLAlchemyJobStore(url=f"sqlite:///{scheduler_path}")
        self._scheduler = BackgroundScheduler(
            jobstores={"default": jobstore},
            timezone=timezone_name,
        )
        if autostart:
            self.start()

    def start(self) -> None:
        with self._lock:
            if not self._scheduler.running:
                self._scheduler.start(paused=False)

    def shutdown(self, wait: bool = False) -> None:
        with self._lock:
            if self._scheduler.running:
                self._scheduler.shutdown(wait=wait)

    @property
    def running(self) -> bool:
        return bool(self._scheduler.running)

    # ----- CRUD ---------------------------------------------------------

    def create(
        self,
        name: str,
        interval_min: int,
        from_time: str,
        to_time: str,
        days: list[str],
        enabled: bool = True,
        dest_filter: str | None = None,
        conditions: str | None = None,
    ) -> Schedule:
        sched_id = uuid.uuid4().hex
        days_csv = ",".join(days)
        with self._db.tx() as conn:
            conn.execute(
                """
                INSERT INTO schedules(id, name, interval_min, from_time, to_time,
                                      days_csv, enabled, dest_filter, conditions)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sched_id,
                    name,
                    int(interval_min),
                    from_time,
                    to_time,
                    days_csv,
                    int(enabled),
                    dest_filter,
                    conditions,
                ),
            )
        self._sync_job(sched_id, interval_min, enabled)
        return self.get(sched_id)  # type: ignore[return-value]

    def update(
        self,
        sched_id: str,
        *,
        name: str | None = None,
        interval_min: int | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        days: list[str] | None = None,
        enabled: bool | None = None,
        dest_filter: str | None = None,
        conditions: str | None = None,
    ) -> Schedule | None:
        existing = self.get(sched_id)
        if existing is None:
            return None
        sets: list[str] = []
        params: list[Any] = []
        for key, val in [
            ("name", name),
            ("interval_min", interval_min),
            ("from_time", from_time),
            ("to_time", to_time),
            ("dest_filter", dest_filter),
            ("conditions", conditions),
        ]:
            if val is not None:
                sets.append(f"{key}=?")
                params.append(val)
        if days is not None:
            sets.append("days_csv=?")
            params.append(",".join(days))
        if enabled is not None:
            sets.append("enabled=?")
            params.append(int(enabled))
        if not sets:
            return existing
        sets.append("updated_at=datetime('now')")
        params.append(sched_id)
        with self._db.tx() as conn:
            conn.execute(f"UPDATE schedules SET {', '.join(sets)} WHERE id=?", params)
        updated = self.get(sched_id)
        if updated is not None:
            self._sync_job(updated.id, updated.interval_min, updated.enabled)
        return updated

    def delete(self, sched_id: str) -> bool:
        with self._db.tx() as conn:
            cur = conn.execute("DELETE FROM schedules WHERE id=?", (sched_id,))
        try:
            self._scheduler.remove_job(sched_id)
        except Exception:  # noqa: BLE001
            pass
        return cur.rowcount > 0

    def get(self, sched_id: str) -> Schedule | None:
        with self._db.connect() as conn:
            row = conn.execute("SELECT * FROM schedules WHERE id=?", (sched_id,)).fetchone()
        return _row(row) if row else None

    def list(self) -> list[Schedule]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM schedules ORDER BY created_at"
            ).fetchall()
        return [_row(r) for r in rows]

    def pause_all(self) -> None:
        with self._lock:
            self._scheduler.pause()

    def resume_all(self) -> None:
        with self._lock:
            self._scheduler.resume()

    def next_fire_time(self) -> datetime | None:
        nxt: datetime | None = None
        for job in self._scheduler.get_jobs():
            if job.next_run_time is None:
                continue
            if nxt is None or job.next_run_time < nxt:
                nxt = job.next_run_time
        return nxt

    def active_count(self) -> int:
        return sum(1 for j in self._scheduler.get_jobs() if j.next_run_time is not None)

    # ----- internal -----------------------------------------------------

    def _sync_job(self, sched_id: str, interval_min: int, enabled: bool) -> None:
        try:
            self._scheduler.remove_job(sched_id)
        except Exception:  # noqa: BLE001
            pass
        if not enabled:
            return
        trigger = IntervalTrigger(minutes=max(1, int(interval_min)))
        self._scheduler.add_job(
            fire_capture,
            trigger=trigger,
            id=sched_id,
            args=[sched_id],
            replace_existing=True,
            misfire_grace_time=max(60, (int(interval_min) * 60) // 2),
            coalesce=True,
            max_instances=1,
        )

    def hydrate_from_db(self) -> None:
        """Recreate APScheduler jobs from the schedules table — call at startup."""
        for s in self.list():
            self._sync_job(s.id, s.interval_min, s.enabled)

    def remove_all_jobs(self) -> None:
        for job in self._scheduler.get_jobs():
            try:
                self._scheduler.remove_job(job.id)
            except Exception:  # noqa: BLE001
                pass


_engine: ScheduleEngine | None = None
_engine_lock = threading.Lock()


def get_engine() -> ScheduleEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = ScheduleEngine(autostart=False)
    return _engine


def reset_engine_singleton() -> None:
    global _engine
    with _engine_lock:
        if _engine is not None:
            try:
                _engine.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                pass
        _engine = None


def _safe_path(p: Path) -> str:
    return str(p)
