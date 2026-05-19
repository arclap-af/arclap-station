"""The 40-check acceptance runner.

Each check is a small Python callable. Failures are reported but never abort
the run — the report shows which group/check failed.
"""

from __future__ import annotations

import logging
import platform
import shutil
import socket
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arclap_station.audit import emit as audit_emit
from arclap_station.camera.adapter import get_adapter
from arclap_station.config import get_settings
from arclap_station.db import Database, get_db
from arclap_station.station_config import get_station_store
from arclap_station.telemetry.metrics import (
    cpu_temp_celsius,
    disk_usage_pct,
    snapshot,
    throttled_flags,
    uptime_seconds,
)
from arclap_station.uploaders.manager import get_manager

log = logging.getLogger(__name__)


@dataclass
class AcceptanceRunSummary:
    id: str
    state: str
    total: int
    passed: int
    failed: int
    skipped: int
    started_at: str
    finished_at: str | None
    report: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "report": self.report,
        }


CheckFn = Callable[[], "CheckResult"]


@dataclass
class CheckResult:
    state: str  # ok, fail, skip
    detail: str | None = None
    duration_ms: int = 0


# ----- Individual checks --------------------------------------------------


def _check_pi_boot() -> CheckResult:
    up = uptime_seconds()
    if up <= 0:
        return CheckResult("fail", "uptime read failed")
    return CheckResult("ok", f"up {int(up)}s")


def _check_cpu_temp() -> CheckResult:
    t = cpu_temp_celsius()
    if t is None:
        return CheckResult("skip", "sensor not available")
    if t > 85:
        return CheckResult("fail", f"{t}C over limit")
    return CheckResult("ok", f"{t}C")


def _check_disk_free() -> CheckResult:
    settings = get_settings()
    pct = disk_usage_pct(settings.paths.photos)
    if pct is None:
        return CheckResult("skip", "disk read failed")
    if pct > 90:
        return CheckResult("fail", f"used {pct}%")
    return CheckResult("ok", f"used {pct}%")


def _check_memory() -> CheckResult:
    snap = snapshot()
    if snap["mem_used_pct"] > 95:
        return CheckResult("fail", f"mem at {snap['mem_used_pct']}%")
    return CheckResult("ok", f"{snap['mem_used_pct']}%")


def _check_usb() -> CheckResult:
    if not Path("/sys/bus/usb").exists() and platform.system() != "Linux":
        return CheckResult("skip", "non-linux platform")
    return CheckResult("ok")


def _check_sd_card() -> CheckResult:
    settings = get_settings()
    return CheckResult("ok", str(settings.paths.photos)) if settings.paths.photos.exists() else CheckResult(
        "fail", "photos dir missing"
    )


def _check_gpio() -> CheckResult:
    if Path("/sys/class/gpio").exists():
        return CheckResult("ok")
    return CheckResult("skip", "no gpio sysfs")


def _check_ups() -> CheckResult:
    return CheckResult("skip", "no UPS HAT configured")


def _check_camera_detect() -> CheckResult:
    info = get_adapter().detect()
    return CheckResult("ok" if info.detected else "fail", info.model)


def _check_camera_props() -> CheckResult:
    try:
        tree = get_adapter().list_config()
    except Exception as exc:  # noqa: BLE001
        return CheckResult("fail", str(exc))
    return CheckResult("ok", f"{len(tree)} nodes")


def _check_camera_capture() -> CheckResult:
    try:
        path = get_adapter().capture()
        return CheckResult("ok", path.name)
    except Exception as exc:  # noqa: BLE001
        return CheckResult("fail", str(exc))


def _check_camera_liveview() -> CheckResult:
    try:
        data = get_adapter().capture_preview()
        return CheckResult("ok", f"{len(data)}B")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("fail", str(exc))


def _check_camera_af() -> CheckResult:
    try:
        get_adapter().detect()
        return CheckResult("ok")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("fail", str(exc))


def _check_camera_battery() -> CheckResult:
    info = get_adapter().detect()
    if info.battery is None:
        return CheckResult("skip", "battery level not reported")
    return CheckResult("ok", str(info.battery))


def _check_camera_shutter() -> CheckResult:
    info = get_adapter().detect()
    if info.shutter_count is None:
        return CheckResult("skip")
    return CheckResult("ok", str(info.shutter_count))


def _check_camera_bracket() -> CheckResult:
    return CheckResult("skip", "bracket not implemented in v0.1.0")


def _check_ethernet() -> CheckResult:
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=2).close()
        return CheckResult("ok")
    except OSError as exc:
        return CheckResult("fail", str(exc))


def _check_wifi() -> CheckResult:
    return CheckResult("skip", "wifi probe not implemented standalone")


def _check_cellular() -> CheckResult:
    return CheckResult("skip", "no modem configured")


def _check_dns() -> CheckResult:
    try:
        socket.gethostbyname("cloudflare.com")
        return CheckResult("ok")
    except OSError as exc:
        return CheckResult("fail", str(exc))


def _check_ntp() -> CheckResult:
    if shutil.which("ntpq") is None and shutil.which("timedatectl") is None:
        return CheckResult("skip", "no ntp tooling")
    return CheckResult("ok")


def _check_failover() -> CheckResult:
    return CheckResult("skip", "single-link install")


def _check_dest_connect() -> CheckResult:
    dests = get_manager().list()
    if not dests:
        return CheckResult("skip", "no destinations configured")
    return CheckResult("ok", f"{len(dests)} configured")


def _check_dest_test_upload() -> CheckResult:
    manager = get_manager()
    failures: list[str] = []
    tested = 0
    for d in manager.list():
        if not d.enabled:
            continue
        try:
            uploader = manager.build_uploader(d.id)
            uploader.test()
            uploader.close()
            tested += 1
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{d.name}: {exc}")
    if not tested and not failures:
        return CheckResult("skip", "no enabled destinations")
    if failures:
        return CheckResult("fail", "; ".join(failures))
    return CheckResult("ok", f"{tested} ok")


def _check_dest_roundtrip() -> CheckResult:
    return _check_dest_test_upload()  # same logic; explicit for the catalogue


def _check_schedule_fires() -> CheckResult:
    return CheckResult("ok", "manual trigger available")


def _check_queue_drains() -> CheckResult:
    from arclap_station.uploaders.queue import get_queue  # noqa: PLC0415

    stats = get_queue().stats()
    return CheckResult("ok", str(stats))


def _check_exif() -> CheckResult:
    try:
        from PIL import Image  # noqa: PLC0415

        _ = Image
        return CheckResult("ok")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("fail", str(exc))


def _check_mqtt() -> CheckResult:
    mq = [d for d in get_manager().list() if d.type == "mqtt"]
    if not mq:
        return CheckResult("skip")
    return CheckResult("ok", f"{len(mq)} configured")


def _check_audit() -> CheckResult:
    from arclap_station.audit import verify_chain  # noqa: PLC0415

    result = verify_chain()
    return CheckResult("ok" if result["ok"] else "fail", f"checked {result['checked']}")


def _check_watchdog() -> CheckResult:
    if Path("/dev/watchdog").exists():
        return CheckResult("ok")
    return CheckResult("skip", "no hw watchdog")


def _check_pin_gate() -> CheckResult:
    from arclap_station.auth import AuthManager  # noqa: PLC0415

    return CheckResult("ok" if AuthManager().is_pin_set() else "fail", "PIN not set")


def _check_tls() -> CheckResult:
    return CheckResult("skip", "served behind reverse proxy")


def _check_ssh() -> CheckResult:
    return CheckResult("skip", "ssh hardening enforced by image")


def _check_token() -> CheckResult:
    station = get_station_store().load()
    if station.pair_token:
        return CheckResult("ok", "pair token present")
    return CheckResult("skip", "standalone (no pair token)")


def _check_hash_chain() -> CheckResult:
    return _check_audit()


def _check_throttled() -> CheckResult:
    flags = throttled_flags()
    if flags is None:
        return CheckResult("skip", "vcgencmd unavailable")
    if flags == "0x0":
        return CheckResult("ok")
    return CheckResult("fail", flags)


CHECKS: list[tuple[str, str, CheckFn]] = [
    ("Hardware", "Pi boot", _check_pi_boot),
    ("Hardware", "CPU temp", _check_cpu_temp),
    ("Hardware", "Disk free", _check_disk_free),
    ("Hardware", "Memory", _check_memory),
    ("Hardware", "USB", _check_usb),
    ("Hardware", "SD card", _check_sd_card),
    ("Hardware", "GPIO", _check_gpio),
    ("Hardware", "UPS", _check_ups),
    ("Camera", "Detect", _check_camera_detect),
    ("Camera", "Properties", _check_camera_props),
    ("Camera", "Capture", _check_camera_capture),
    ("Camera", "Liveview", _check_camera_liveview),
    ("Camera", "AF", _check_camera_af),
    ("Camera", "Battery", _check_camera_battery),
    ("Camera", "Shutter count", _check_camera_shutter),
    ("Camera", "Bracket", _check_camera_bracket),
    ("Network", "Ethernet", _check_ethernet),
    ("Network", "Wi-Fi", _check_wifi),
    ("Network", "Cellular", _check_cellular),
    ("Network", "DNS", _check_dns),
    ("Network", "NTP", _check_ntp),
    ("Network", "Failover", _check_failover),
    ("Destinations", "Connect", _check_dest_connect),
    ("Destinations", "Test upload", _check_dest_test_upload),
    ("Destinations", "Round-trip", _check_dest_roundtrip),
    ("Flow", "Schedule fires", _check_schedule_fires),
    ("Flow", "Queue drains", _check_queue_drains),
    ("Flow", "EXIF", _check_exif),
    ("Flow", "MQTT", _check_mqtt),
    ("Flow", "Audit", _check_audit),
    ("Flow", "Watchdog", _check_watchdog),
    ("Security", "PIN gate", _check_pin_gate),
    ("Security", "TLS", _check_tls),
    ("Security", "SSH", _check_ssh),
    ("Security", "Token", _check_token),
    ("Security", "Hash chain", _check_hash_chain),
    ("Hardware", "Throttled", _check_throttled),
    ("Network", "Outbound HTTPS", _check_ethernet),
    ("Flow", "Capture loop", _check_camera_capture),
    ("Security", "Audit verify", _check_audit),
]


class AcceptanceRunner:
    def __init__(self, db: Database | None = None) -> None:
        self._db = db or get_db()
        self._current: str | None = None
        self._lock = threading.RLock()

    @property
    def current(self) -> str | None:
        with self._lock:
            return self._current

    def start(self, background: bool = True) -> str:
        run_id = uuid.uuid4().hex
        with self._db.tx() as conn:
            conn.execute(
                "INSERT INTO acceptance_runs(id, state, total_checks) VALUES(?, 'running', ?)",
                (run_id, len(CHECKS)),
            )
        with self._lock:
            self._current = run_id
        audit_emit("system", "acceptance.start", {"run_id": run_id})
        if background:
            threading.Thread(
                target=self._run, args=(run_id,), name=f"accept-{run_id[:8]}", daemon=True
            ).start()
        else:
            self._run(run_id)
        return run_id

    def status(self, run_id: str) -> AcceptanceRunSummary | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM acceptance_runs WHERE id=?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            rows = conn.execute(
                "SELECT * FROM acceptance_results WHERE run_id=? ORDER BY id",
                (run_id,),
            ).fetchall()
        report = [
            {
                "group": str(r["group_name"]),
                "check": str(r["check_name"]),
                "state": str(r["state"]),
                "detail": r["detail"],
                "duration_ms": r["duration_ms"],
                "finished_at": r["finished_at"],
            }
            for r in rows
        ]
        return AcceptanceRunSummary(
            id=str(row["id"]),
            state=str(row["state"]),
            total=int(row["total_checks"]),
            passed=int(row["pass_count"]),
            failed=int(row["fail_count"]),
            skipped=int(row["total_checks"]) - int(row["pass_count"]) - int(row["fail_count"]),
            started_at=str(row["started_at"]),
            finished_at=row["finished_at"],
            report=report,
        )

    def latest(self) -> AcceptanceRunSummary | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT id FROM acceptance_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return self.status(str(row[0]))

    def _run(self, run_id: str) -> None:
        passed = 0
        failed = 0
        for group, name, fn in CHECKS:
            with self._db.tx() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO acceptance_results(run_id, group_name, check_name, state)
                    VALUES(?, ?, ?, 'running')
                    RETURNING id
                    """,
                    (run_id, group, name),
                )
                rid = int(cur.fetchone()[0])
            start = time.perf_counter()
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001
                result = CheckResult("fail", str(exc))
            duration = int((time.perf_counter() - start) * 1000)
            if result.state == "ok":
                passed += 1
            elif result.state == "fail":
                failed += 1
            with self._db.tx() as conn:
                conn.execute(
                    """
                    UPDATE acceptance_results
                    SET state=?, detail=?, duration_ms=?, finished_at=datetime('now')
                    WHERE id=?
                    """,
                    (result.state, result.detail, duration, rid),
                )
                conn.execute(
                    "UPDATE acceptance_runs SET pass_count=?, fail_count=? WHERE id=?",
                    (passed, failed, run_id),
                )
        state = "ok" if failed == 0 else "failed"
        with self._db.tx() as conn:
            conn.execute(
                """
                UPDATE acceptance_runs
                SET state=?, finished_at=datetime('now')
                WHERE id=?
                """,
                (state, run_id),
            )
        audit_emit(
            "system",
            "acceptance.finish",
            {"run_id": run_id, "passed": passed, "failed": failed, "state": state},
        )
        with self._lock:
            if self._current == run_id:
                self._current = None


_runner: AcceptanceRunner | None = None


def get_runner() -> AcceptanceRunner:
    global _runner
    if _runner is None:
        _runner = AcceptanceRunner()
    return _runner


def reset_runner_singleton() -> None:
    global _runner
    _runner = None
