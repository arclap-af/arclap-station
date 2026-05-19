"""Diagnostic endpoints — surfaces in the cockpit's Settings → System tab.

Routes:
    GET  /api/diag/support-bundle  → tar.gz download (PIN-gated)
    GET  /api/diag/boot-history    → last 50 boots with reason
    GET  /api/diag/services        → systemctl is-active for every arclap unit
    GET  /api/diag/smart           → smartctl on /dev/mmcblk0 + photo volume
    GET  /api/diag/slow-log        → tail of /var/log/arclap/slow.log
    GET  /api/diag/sentry-status   → whether Sentry crash reporter is wired
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Response

from arclap_station.api.deps import require_session

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/diag", tags=["diag"])

UNITS = [
    "arclap-station",
    "arclap-watchdog.timer",
    "arclap-camera-watchdog.timer",
    "arclap-retention.timer",
    "arclap-backup.timer",
    "arclap-integrity.timer",
    "caddy",
    "systemd-timesyncd",
    "ntp",
    "systemd-resolved",
]


@router.get("/support-bundle")
async def support_bundle(_: dict[str, Any] = Depends(require_session)) -> Response:
    from arclap_station.audit import emit as audit_emit  # noqa: PLC0415
    from arclap_station.diag import build_support_bundle  # noqa: PLC0415

    body, fname = build_support_bundle()
    audit_emit("user", "diag.support_bundle_download", {"size_bytes": len(body)})
    return Response(
        content=body,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/boot-history")
async def boot_history_endpoint(
    limit: int = 50,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    from arclap_station.diag import boot_history  # noqa: PLC0415

    return {"boots": boot_history(limit=max(1, min(200, limit)))}


@router.get("/services")
async def services(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    """Return systemctl is-active for every arclap + critical unit."""
    out: list[dict[str, Any]] = []
    for unit in UNITS:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True, text=True, timeout=2,
            )
            state = r.stdout.strip() or "unknown"
        except (FileNotFoundError, subprocess.SubprocessError):
            state = "unknown"
        try:
            r = subprocess.run(
                ["systemctl", "is-enabled", unit],
                capture_output=True, text=True, timeout=2,
            )
            enabled = r.stdout.strip() or "unknown"
        except (FileNotFoundError, subprocess.SubprocessError):
            enabled = "unknown"
        out.append({
            "unit": unit,
            "active": state,
            "enabled": enabled,
            "ok": state in ("active", "activating"),
        })
    return {"services": out}


@router.get("/smart")
async def smart(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    """Parse `smartctl -A -H` on /dev/mmcblk0 if available.

    The SD card SMART support is patchy (depends on card vendor); we
    surface what's there honestly rather than faking 'ok'.
    """
    candidates = ["/dev/mmcblk0", "/dev/sda", "/dev/nvme0n1"]
    devices: list[dict[str, Any]] = []
    for dev in candidates:
        if not Path(dev).exists():
            continue
        try:
            r = subprocess.run(
                ["smartctl", "-A", "-H", "-j", dev],
                capture_output=True, text=True, timeout=8,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            devices.append({"device": dev, "error": f"smartctl not available: {exc}"})
            continue
        if r.returncode not in (0, 4):  # 4 = no SMART but tool ran
            devices.append({"device": dev, "error": r.stderr.strip()[:200] or "smartctl failed"})
            continue
        try:
            import json as _j  # noqa: PLC0415

            d = _j.loads(r.stdout)
        except (ValueError, KeyError):
            devices.append({"device": dev, "error": "could not parse smartctl JSON"})
            continue
        passed = d.get("smart_status", {}).get("passed", False)
        attrs = d.get("ata_smart_attributes", {}).get("table", [])
        rel = [
            {
                "name": a.get("name"),
                "value": a.get("raw", {}).get("value"),
                "thresh": a.get("thresh"),
                "worst": a.get("worst"),
            }
            for a in attrs
            if a.get("name") in ("Reallocated_Sector_Ct", "Wear_Leveling_Count",
                                  "Total_LBAs_Written", "Percent_Lifetime_Remain",
                                  "Power_On_Hours", "Temperature_Celsius",
                                  "Reported_Uncorrect")
        ]
        devices.append({
            "device": dev,
            "model": d.get("model_name"),
            "serial": d.get("serial_number"),
            "size_bytes": d.get("user_capacity", {}).get("bytes"),
            "passed": passed,
            "attributes": rel,
        })
    return {"devices": devices}


@router.get("/slow-log")
async def slow_log_tail(
    lines: int = 200,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """Tail the slow-query / slow-op log."""
    import os as _os  # noqa: PLC0415

    path = Path(_os.environ.get("ARCLAP_LOG_DIR", "/var/log/arclap")) / "slow.log"
    if not path.exists():
        return {"path": str(path), "lines": []}
    try:
        # Cheap tail — read last N KB and split.
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0, size - 32768))
            tail = f.read().decode("utf-8", errors="replace")
        rows = [line for line in tail.splitlines() if line.strip()][-lines:]
        return {"path": str(path), "lines": rows}
    except OSError as exc:
        return {"path": str(path), "error": str(exc), "lines": []}


@router.get("/sentry-status")
async def sentry_status(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    import os as _os  # noqa: PLC0415

    dsn = _os.environ.get("SENTRY_DSN", "")
    return {
        "enabled": bool(dsn),
        # Don't echo the DSN itself — it's secret. Just whether it's set
        # and the optional environment tag.
        "environment": _os.environ.get("SENTRY_ENVIRONMENT", "production"),
    }


# ----- Support tunnel (WireGuard, §12.5.6) ------------------------------


@router.get("/tunnel")
async def tunnel_status(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    from arclap_station.cloud.wireguard import status as wg_status  # noqa: PLC0415

    return wg_status()


@router.post("/tunnel/up")
async def tunnel_up(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    from arclap_station.cloud.wireguard import up as wg_up  # noqa: PLC0415

    return wg_up()


@router.post("/tunnel/down")
async def tunnel_down(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    from arclap_station.cloud.wireguard import down as wg_down  # noqa: PLC0415

    return wg_down()


# ----- Request percentiles -----------------------------------------------


@router.get("/percentiles")
async def request_percentiles(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    """p50/p95 latency per endpoint, computed from in-memory histogram."""
    from arclap_station.metrics_prom import percentile_summary  # noqa: PLC0415

    return {"endpoints": percentile_summary()}
