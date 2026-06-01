"""Station self-test — the single source of truth for "is this station OK".

Runs a battery of fail-soft probes and returns a normalised result:

    {
      "overall": "ok" | "warn" | "bad",
      "score": 0..100,                       # share of checks that are ok
      "ran_at": "2026-05-21T08:00:00Z",
      "checks": [
        {"id","label","status","detail","hint"}, ...
      ]
    }

Each check is independent and wrapped so a probe that itself throws
degrades to `unknown` (counted as a warn) instead of failing the whole
self-test. The cockpit's Health view + the alerting/heartbeat loops all
consume this one function so there is exactly one definition of healthy.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

# Status ordering — worst wins when aggregating.
_RANK = {"ok": 0, "unknown": 1, "warn": 2, "bad": 3}


@dataclass
class Check:
    id: str
    label: str
    status: str  # ok | warn | bad | unknown
    detail: str
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _worst(statuses: list[str]) -> str:
    if not statuses:
        return "unknown"
    return max(statuses, key=lambda s: _RANK.get(s, 1))


# ── individual probes ────────────────────────────────────────────────
# Each returns a Check. Never raises — the runner also guards, but
# probes keep their own try/except so a partial failure still yields a
# meaningful detail string.

def _check_camera() -> Check:
    try:
        from arclap_station.camera import health as cam_health  # noqa: PLC0415

        st = cam_health.read_state()
        ok = bool(st.get("ok"))
        last_err = st.get("last_error")
        age = cam_health.beacon_age_sec()
        if ok:
            return Check("camera", "Camera", "ok", "Detected and responding.")
        if last_err:
            return Check(
                "camera", "Camera", "bad",
                f"Last error: {last_err}",
                "Half-press the shutter to wake it, then press Reconnect on the Camera page. "
                "If it persists, check the USB cable and that the body is in PC/PTP mode.",
            )
        if age is not None and age > 3600:
            return Check(
                "camera", "Camera", "warn",
                "No recent camera activity.",
                "No capture in over an hour — confirm a schedule is active or trigger a manual capture.",
            )
        return Check("camera", "Camera", "warn", "Camera state unknown.", "Open the Camera page to probe.")
    except Exception as exc:  # noqa: BLE001
        return Check("camera", "Camera", "unknown", f"probe failed: {exc}")


def _check_disk() -> Check:
    try:
        from arclap_station.config import get_settings  # noqa: PLC0415

        root = get_settings().paths.photos
        target = root if root.exists() else Path(root.anchor or "/")
        u = shutil.disk_usage(target)
        pct = (u.used / u.total * 100) if u.total else 0
        free_gb = u.free / 1e9
        detail = f"{pct:.0f}% used · {free_gb:.1f} GB free"
        if pct >= 95:
            return Check("disk", "Storage", "bad", detail,
                         "Disk nearly full — captures will start being refused. Free space or check retention policy.")
        if pct >= 85:
            return Check("disk", "Storage", "warn", detail,
                         "Disk filling up — the retention sweep should reclaim space; verify uploads are draining.")
        return Check("disk", "Storage", "ok", detail)
    except Exception as exc:  # noqa: BLE001
        return Check("disk", "Storage", "unknown", f"probe failed: {exc}")


def _check_clock() -> Check:
    """NTP sync state — timelapses depend on accurate timestamps."""
    try:
        out = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True, text=True, timeout=4,
        )
        val = out.stdout.strip().lower()
        if val == "yes":
            return Check("clock", "Clock / NTP", "ok", "System clock is NTP-synchronised.")
        if val == "no":
            return Check("clock", "Clock / NTP", "warn", "Clock not NTP-synchronised.",
                         "Photos may carry skewed timestamps. Check the NTP server in Settings → Network "
                         "(an RTC module keeps time accurate when NTP is unreachable).")
        return Check("clock", "Clock / NTP", "unknown", "Could not read NTP sync state.")
    except (FileNotFoundError, subprocess.SubprocessError):
        return Check("clock", "Clock / NTP", "unknown", "timedatectl unavailable on this host.")
    except Exception as exc:  # noqa: BLE001
        return Check("clock", "Clock / NTP", "unknown", f"probe failed: {exc}")


def _check_destinations() -> Check:
    try:
        from arclap_station.uploaders.manager import get_manager  # noqa: PLC0415

        dests = get_manager().list()
        enabled = [d for d in dests if d.enabled]
        if not dests:
            return Check("destinations", "Destinations", "warn", "No destinations configured.",
                         "Photos stay on the SD card only. Add a destination so captures are backed up off-device.")
        if not enabled:
            return Check("destinations", "Destinations", "warn",
                         f"{len(dests)} configured, none enabled.",
                         "Every destination is disabled — captured photos won't upload anywhere.")
        with_error = [d for d in enabled if d.last_error]
        if with_error:
            names = ", ".join(d.name for d in with_error[:3])
            return Check("destinations", "Destinations", "warn",
                         f"{len(enabled)} enabled · {len(with_error)} with errors ({names})",
                         "One or more destinations last reported an error. Open Destinations to Test them.")
        return Check("destinations", "Destinations", "ok", f"{len(enabled)} enabled, all healthy.")
    except Exception as exc:  # noqa: BLE001
        return Check("destinations", "Destinations", "unknown", f"probe failed: {exc}")


def _check_upload_queue() -> Check:
    try:
        from arclap_station.db import get_db  # noqa: PLC0415

        with get_db().connect() as conn:
            pending = conn.execute(
                "SELECT COUNT(*) FROM upload_queue WHERE state NOT IN ('ok','failed_permanent')"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM upload_queue WHERE state='failed_permanent'"
            ).fetchone()[0]
        pending, failed = int(pending), int(failed)
        if failed > 0:
            return Check("queue", "Upload queue", "warn",
                         f"{pending} pending · {failed} permanently failed",
                         "Some uploads exhausted their retries. Check the destination and re-trigger from Gallery.")
        if pending > 200:
            return Check("queue", "Upload queue", "warn", f"{pending} pending uploads",
                         "Large backlog — the link may be slow or a destination is down.")
        return Check("queue", "Upload queue", "ok", f"{pending} pending · 0 failed")
    except Exception as exc:  # noqa: BLE001
        return Check("queue", "Upload queue", "unknown", f"probe failed: {exc}")


def _check_thermal() -> Check:
    try:
        from arclap_station.telemetry.metrics import cpu_temp_celsius, throttled_flags  # noqa: PLC0415

        t = cpu_temp_celsius()
        flags = throttled_flags()
        throttling = flags not in (None, "0x0")
        if t is None:
            return Check("thermal", "Temperature", "unknown", "No temperature sensor readable.")
        detail = f"{t:.0f}°C" + (f" · throttled ({flags})" if throttling else "")
        if t >= 80 or throttling:
            return Check("thermal", "Temperature", "bad", detail,
                         "Pi is hot and may be throttling — improve airflow / add a heatsink or fan. "
                         "Sustained heat shortens SD-card and camera life.")
        if t >= 70:
            return Check("thermal", "Temperature", "warn", detail,
                         "Running warm — fine short-term but watch it in summer / enclosed boxes.")
        return Check("thermal", "Temperature", "ok", detail)
    except Exception as exc:  # noqa: BLE001
        return Check("thermal", "Temperature", "unknown", f"probe failed: {exc}")


def _check_memory() -> Check:
    try:
        import psutil  # noqa: PLC0415

        vm = psutil.virtual_memory()
        pct = vm.percent
        detail = f"{pct:.0f}% used · {vm.used // (1024*1024)} / {vm.total // (1024*1024)} MB"
        if pct >= 95:
            return Check("memory", "Memory", "warn", detail,
                         "Memory pressure high — the service has a 1 GB cap; a restart will reclaim it.")
        return Check("memory", "Memory", "ok", detail)
    except Exception as exc:  # noqa: BLE001
        return Check("memory", "Memory", "unknown", f"probe failed: {exc}")


def _check_power() -> Check:
    """UPS / power state. `ok` with 'no UPS' is normal on wired stations."""
    try:
        from arclap_station.hardware.ups import read_ups  # noqa: PLC0415

        ups = read_ups()
        if ups is None or not ups.get("present"):
            return Check("power", "Power / UPS", "ok", "Wired power · no UPS fitted.")
        pct = ups.get("percent")
        on_battery = ups.get("on_battery")
        detail = f"{pct}%" + (" · ON BATTERY" if on_battery else " · mains")
        if on_battery and (pct is not None and pct < 20):
            return Check("power", "Power / UPS", "bad", detail,
                         "Running on battery and low — station will shut down safely soon to protect the SD card.")
        if on_battery:
            return Check("power", "Power / UPS", "warn", detail, "Mains power lost — running on UPS battery.")
        return Check("power", "Power / UPS", "ok", detail)
    except Exception as exc:  # noqa: BLE001
        return Check("power", "Power / UPS", "unknown", f"probe failed: {exc}")


_PROBES: list[Callable[[], Check]] = [
    _check_camera,
    _check_disk,
    _check_clock,
    _check_destinations,
    _check_upload_queue,
    _check_thermal,
    _check_memory,
    _check_power,
]


def run_selftest() -> dict[str, Any]:
    """Run every probe and aggregate. Never raises."""
    checks: list[Check] = []
    for probe in _PROBES:
        try:
            checks.append(probe())
        except Exception as exc:  # noqa: BLE001 - belt-and-braces; probes already guard
            checks.append(Check(getattr(probe, "__name__", "check"), "Check", "unknown", f"crashed: {exc}"))

    statuses = [c.status for c in checks]
    overall = _worst(statuses)
    ok_count = sum(1 for s in statuses if s == "ok")
    score = round(ok_count / len(statuses) * 100) if statuses else 0

    return {
        "overall": overall,
        "score": score,
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "checks": [c.to_dict() for c in checks],
    }
