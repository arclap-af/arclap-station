"""APScheduler engine with SQLAlchemyJobStore.

Each schedule lives in the `schedules` table of state.db AND has a paired job
in scheduler.db so APScheduler can keep firing across reboots. The two are
linked by id.
"""

from __future__ import annotations

import json
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
from arclap_station.photos.exif import extract_exif
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

    @property
    def conditions_dict(self) -> dict[str, Any]:
        """Parse the `conditions` JSON string into a dict.

        `conditions` is stored as a JSON string in the DB (NULL allowed)
        so we can add new flags without a schema migration. Returns an
        empty dict on null / malformed / non-object payloads.
        """
        if not self.conditions:
            return {}
        try:
            parsed = json.loads(self.conditions)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def to_dict(self) -> dict[str, Any]:
        # Resolve the actual next fire time from APScheduler for this
        # specific job (different from engine.next_fire_time() which
        # returns the soonest across all jobs).
        next_at: str | None = None
        try:
            global _engine
            if _engine is not None and _engine._scheduler is not None:
                job = _engine._scheduler.get_job(self.id)
                if job is not None and job.next_run_time is not None:
                    next_at = job.next_run_time.isoformat()
        except Exception:  # noqa: BLE001
            pass
        cond = self.conditions_dict
        return {
            "id": self.id,
            "name": self.name,
            "interval_min": self.interval_min,
            "from_time": self.from_time,
            "to_time": self.to_time,
            "days": self.days,
            "enabled": self.enabled,
            "dest_filter": self.dest_filter,
            # `conditions` retained as the raw JSON string for any
            # forensic / migration use; the UI reads the flat flags
            # below so it doesn't have to parse JSON.
            "conditions": self.conditions,
            # Flat-keyed flags from the conditions JSON. Default both
            # to True — that's the safer behaviour for a schedule
            # whose flags are unset (existing pre-feature rows) and
            # matches the cockpit's "ON by default" UI.
            "skip_disk_full": bool(cond.get("skip_disk_full", True)),
            "skip_destinations_offline": bool(
                cond.get("skip_destinations_offline", True)
            ),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "next_fire_at": next_at,
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
    # Per-schedule skip-when flags from the conditions JSON. Default
    # ON for both — matches the cockpit's default and is the safer
    # behaviour for an unset (pre-feature) schedule.
    cond = sched.conditions_dict
    skip_disk_full = bool(cond.get("skip_disk_full", True))
    skip_destinations_offline = bool(cond.get("skip_destinations_offline", True))

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

    # Skip-when-destinations-offline gate. We treat "offline" here as
    # "no enabled destination matches this schedule's dest_filter".
    # A truly unreachable destination still surfaces via its queue
    # retries; this gate catches the upstream case where the operator
    # has disabled every destination this schedule would route to,
    # which would otherwise pile photos into a queue with nowhere
    # to drain.
    if skip_destinations_offline:
        try:
            from arclap_station.uploaders.manager import get_manager  # noqa: PLC0415
            wanted_ids: set[str] | None = None
            if sched.dest_filter:
                wanted_ids = {x.strip() for x in sched.dest_filter.split(",") if x.strip()}
            enabled_matches = [
                d for d in get_manager().list()
                if d.enabled and (wanted_ids is None or d.id in wanted_ids)
            ]
            if not enabled_matches:
                log.info("skipping capture: no enabled destinations match filter")
                return {
                    "ok": False,
                    "skipped": True,
                    "reason": "destinations_offline",
                }
        except Exception as exc:  # noqa: BLE001
            log.debug("destinations-offline check failed (continuing): %s", exc)

    adapter = get_adapter()
    info = adapter.detect()
    if not info.detected:
        return {"ok": False, "skipped": True, "reason": "no_camera"}

    # Disk-pressure gate. Two thresholds:
    #   * 2 % free — HARD floor. Always enforced regardless of the
    #     schedule's preference, to prevent a card-full crash that
    #     can corrupt the SQLite WAL.
    #   * 10 % free — SOFT threshold, gated by `skip_disk_full`. The
    #     cockpit UI exposes this as the "Disk > 90 %" toggle. With
    #     the toggle off, we trust the operator (and the nightly
    #     retention sweep) to manage capacity.
    try:
        import shutil as _shutil  # noqa: PLC0415
        photos_root = get_settings().paths.photos
        usage = _shutil.disk_usage(photos_root)
        free_pct = (usage.free / usage.total) * 100 if usage.total > 0 else 100
        if free_pct < 2.0:
            try:
                from arclap_station.audit import emit as _audit  # noqa: PLC0415
                _audit("system", "capture.refused_disk_full",
                       {"schedule_id": schedule_id, "free_pct": round(free_pct, 2)})
            except Exception:  # noqa: BLE001
                pass
            return {"ok": False, "skipped": True, "reason": "disk_full"}
        if skip_disk_full and free_pct < 10.0:
            log.info(
                "skipping capture: disk %.1f%% free, schedule skip_disk_full=on",
                free_pct,
            )
            return {"ok": False, "skipped": True, "reason": "disk_high"}
    except (OSError, ValueError, ImportError):
        pass  # fall through — capture will fail loudly if disk is truly dead

    photo_path = adapter.capture()
    # Same EXIF + watermark/rotate path as /api/camera/capture.
    try:
        from arclap_station.photos.watermark import apply_watermark_and_rotate  # noqa: PLC0415
        apply_watermark_and_rotate(photo_path)
    except Exception:  # noqa: BLE001
        pass
    exif, width, height = extract_exif(photo_path)

    # Perceptual-hash dedup: if this frame is near-identical to the last
    # one taken under this schedule, drop it on the floor. Saves SD card
    # + bandwidth on static scenes overnight.
    try:
        from arclap_station.photos.dedup import (  # noqa: PLC0415
            compute_dhash, store_hash, maybe_drop_duplicate, DEFAULT_THRESHOLD,
        )
        from arclap_station.station_config import get_station_store  # noqa: PLC0415

        cfg_obj = get_station_store().load()
        threshold = getattr(cfg_obj, "dedup_threshold", None) or DEFAULT_THRESHOLD
        # Only auto-drop if the operator explicitly enabled dedup
        # (presence of `dedup_threshold` field in station.json).
        if getattr(cfg_obj, "dedup_threshold", None) is not None:
            if maybe_drop_duplicate(photo_path, schedule_id, threshold):
                return {"ok": False, "skipped": True, "reason": "duplicate"}
        new_hash_value = compute_dhash(photo_path)
    except Exception:  # noqa: BLE001
        new_hash_value = None

    store = get_store()
    record = store.register(
        photo_path, exif=exif, width=width, height=height, job_id=schedule_id
    )
    if new_hash_value is not None:
        try:
            store_hash(record.id, new_hash_value)
        except Exception:  # noqa: BLE001
            pass
    dest_ids = list_destination_ids(sched.dest_filter)
    if dest_ids:
        get_queue().enqueue(record.id, dest_ids)
    else:
        # Orphan capture: photo is on the SD card but no enabled
        # destination accepts it. Without this audit emit the photo
        # would silently sit local forever, the operator would see
        # the captures-today counter going up but nothing landing
        # at the configured destination, and have no breadcrumb
        # explaining why.
        try:
            from arclap_station.audit import emit as _audit  # noqa: PLC0415
            _audit(
                "system",
                "capture.orphan",
                {
                    "photo_id": record.id,
                    "schedule_id": schedule_id,
                    "dest_filter": sched.dest_filter,
                    "reason": "no_matching_destination",
                },
            )
        except Exception:  # noqa: BLE001
            pass
        log.warning(
            "capture %s has no destination (filter=%s) — photo stays local",
            record.id,
            sched.dest_filter,
        )
    return {
        "ok": True,
        "photo_id": record.id,
        "destinations": len(dest_ids),
        "iso": exif.get("iso") if exif else None,
        "shutter": exif.get("shutter") if exif else None,
        "aperture": exif.get("aperture") if exif else None,
    }


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
        skip_disk_full: bool = True,
        skip_destinations_offline: bool = True,
    ) -> Schedule:
        sched_id = uuid.uuid4().hex
        days_csv = ",".join(days)
        # If the caller passed a raw `conditions` JSON string, honour it.
        # Otherwise build the JSON from the flat flags. Both flags
        # default to True (safer behaviour + matches the UI's default).
        conditions_str = conditions if conditions is not None else json.dumps({
            "skip_disk_full": bool(skip_disk_full),
            "skip_destinations_offline": bool(skip_destinations_offline),
        })
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
                    conditions_str,
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
        skip_disk_full: bool | None = None,
        skip_destinations_offline: bool | None = None,
        clear_dest_filter: bool = False,
    ) -> Schedule | None:
        existing = self.get(sched_id)
        if existing is None:
            return None
        sets: list[str] = []
        params: list[Any] = []
        # `dest_filter` needs special handling — None means "leave alone"
        # but the caller also needs a way to clear it (set to NULL =
        # "All destinations"). The frontend sends dest_filter=None to
        # mean "clear" so we always write the value, including NULL.
        # Using a sentinel arg `clear_dest_filter` keeps the existing
        # signature working for non-API callers who genuinely don't
        # want to touch this field.
        for key, val in [
            ("name", name),
            ("interval_min", interval_min),
            ("from_time", from_time),
            ("to_time", to_time),
        ]:
            if val is not None:
                sets.append(f"{key}=?")
                params.append(val)
        if dest_filter is not None or clear_dest_filter:
            sets.append("dest_filter=?")
            params.append(dest_filter)
        # Merge skip_* flags into the existing conditions JSON if
        # either flag was provided. We always write the FULL conditions
        # JSON so a partial update never strips the other flag.
        if (
            skip_disk_full is not None
            or skip_destinations_offline is not None
            or conditions is not None
        ):
            if conditions is not None:
                # Caller passed raw JSON — trust it verbatim.
                merged_cond = conditions
            else:
                cond = existing.conditions_dict.copy()
                if skip_disk_full is not None:
                    cond["skip_disk_full"] = bool(skip_disk_full)
                if skip_destinations_offline is not None:
                    cond["skip_destinations_offline"] = bool(
                        skip_destinations_offline
                    )
                merged_cond = json.dumps(cond)
            sets.append("conditions=?")
            params.append(merged_cond)
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
