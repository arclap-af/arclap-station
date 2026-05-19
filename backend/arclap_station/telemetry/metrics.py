"""System telemetry: CPU, memory, disk, temperature, throttling, uptime."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import psutil

from arclap_station.config import get_settings

log = logging.getLogger(__name__)

_BOOT_TIME: float | None = None


def _read_first_line(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def cpu_temp_celsius() -> float | None:
    # /sys/class/thermal/thermal_zone0/temp reads as millidegrees
    raw = _read_first_line(Path("/sys/class/thermal/thermal_zone0/temp"))
    if raw and raw.isdigit():
        return round(int(raw) / 1000.0, 1)
    try:
        temps = psutil.sensors_temperatures()  # type: ignore[attr-defined]
        for name, entries in temps.items():
            if entries:
                return float(entries[0].current)
            _ = name
    except Exception:  # noqa: BLE001
        pass
    return None


def vcgencmd_get(arg: str) -> str | None:
    try:
        out = subprocess.run(
            ["vcgencmd", arg],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return out.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        return None


def throttled_flags() -> str | None:
    raw = vcgencmd_get("get_throttled")
    if raw and "=" in raw:
        return raw.split("=", 1)[1]
    return None


def uptime_seconds() -> float:
    global _BOOT_TIME
    if _BOOT_TIME is None:
        try:
            _BOOT_TIME = psutil.boot_time()
        except Exception:  # noqa: BLE001
            _BOOT_TIME = time.time()
    return max(0.0, time.time() - (_BOOT_TIME or time.time()))


def disk_usage_pct(path: Path) -> float | None:
    try:
        if path.exists():
            target = path
        else:
            target = Path(path.anchor or "/")
        u = shutil.disk_usage(target)
        if u.total <= 0:
            return None
        return round(u.used / u.total * 100, 1)
    except OSError:
        return None


# Cached counter snapshot from the last call to snapshot() — used to
# compute the instantaneous network throughput as bytes-delta / elapsed.
_LAST_NET: dict[str, Any] | None = None


def network_throughput_mbps() -> float | None:
    """Compute the host's aggregate TX+RX bandwidth in Mbps over the
    interval since the previous call. Returns None on the first call
    (we need at least two samples)."""
    global _LAST_NET
    try:
        counters = psutil.net_io_counters(pernic=False)
    except Exception:  # noqa: BLE001
        return None
    now = time.monotonic()
    cur_bytes = counters.bytes_recv + counters.bytes_sent
    prev = _LAST_NET
    _LAST_NET = {"ts": now, "bytes": cur_bytes}
    if prev is None:
        return None
    dt = max(0.001, now - prev["ts"])
    dbytes = max(0, cur_bytes - prev["bytes"])
    mbps = (dbytes * 8) / (1_000_000.0 * dt)
    return round(mbps, 2)


def disk_free_bytes(path: Path) -> int:
    try:
        target = path if path.exists() else Path(path.anchor or "/")
        return int(shutil.disk_usage(target).free)
    except OSError:
        return 0


def disk_total_bytes(path: Path) -> int:
    try:
        target = path if path.exists() else Path(path.anchor or "/")
        return int(shutil.disk_usage(target).total)
    except OSError:
        return 0


def snapshot() -> dict[str, Any]:
    settings = get_settings()
    try:
        load_avg = psutil.getloadavg()
        cpu_load = round(load_avg[0], 2)
    except (AttributeError, OSError):
        cpu_load = round(psutil.cpu_percent(interval=None) / 100.0, 2)

    vm = psutil.virtual_memory()
    mem_used_pct = round(vm.percent, 1)

    photos_root = settings.paths.photos
    disk_pct = disk_usage_pct(photos_root)

    return {
        "cpu_temp_c": cpu_temp_celsius(),
        "cpu_load": cpu_load,
        "cpu_pct": round(psutil.cpu_percent(interval=None), 1),
        "mem_used_pct": mem_used_pct,
        "mem_total_mb": int(vm.total / (1024 * 1024)),
        "mem_used_mb": int(vm.used / (1024 * 1024)),
        "disk_used_pct": disk_pct,
        "disk_free_bytes": disk_free_bytes(photos_root),
        "disk_total_bytes": disk_total_bytes(photos_root),
        "uptime_seconds": int(uptime_seconds()),
        "throttled_flags": throttled_flags(),
        "boot_time": int(psutil.boot_time()) if hasattr(psutil, "boot_time") else None,
        "network_throughput_mbps": network_throughput_mbps(),
    }
