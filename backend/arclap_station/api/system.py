"""System router: /api/system/* — fleet station-card + software update check.

This is the station-side contract a future fleet dashboard consumes:
one compact "everything about this one station" payload, plus a
read-only "is there a newer version on GitHub" check. Neither endpoint
mutates anything — the actual update is applied via the documented
install path (curl one-liner / reinstall helper), gated by the
operator, not auto-fired here.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends

from arclap_station import __version__
from arclap_station.api.deps import require_session

router = APIRouter(prefix="/api/system", tags=["system"])

# Public repo — the station can query tags unauthenticated (rate-limited
# to 60 req/h per IP, far above our periodic-check needs).
_GITHUB_TAGS_URL = "https://api.github.com/repos/arclap-af/arclap-station/tags"
_RELEASES_URL = "https://github.com/arclap-af/arclap-station/releases"


def _parse_semver(tag: str) -> tuple[int, int, int] | None:
    """v0.9.0 / 0.9.0 -> (0, 9, 0). Returns None for non-semver tags."""
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", tag.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


@router.get("/info")
async def system_info(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    """Consolidated station card — the single payload a fleet view needs
    to render one station: identity, version, health, and the headline
    activity counters. Cheap; safe to poll."""
    from arclap_station.health import alerts as _alerts  # noqa: PLC0415
    from arclap_station.station_config import get_station_store  # noqa: PLC0415
    from arclap_station.telemetry.metrics import snapshot, uptime_seconds  # noqa: PLC0415
    from arclap_station.db import get_db  # noqa: PLC0415

    cfg = get_station_store().load()
    health_state = _alerts.read_state()
    metrics = snapshot()

    captures_today = 0
    queue_pending = 0
    queue_failed = 0
    try:
        with get_db().connect() as conn:
            captures_today = int(conn.execute(
                "SELECT COUNT(*) FROM photos WHERE captured_at >= date('now')"
            ).fetchone()[0])
            queue_pending = int(conn.execute(
                "SELECT COUNT(*) FROM upload_queue WHERE state NOT IN ('ok','failed_permanent')"
            ).fetchone()[0])
            queue_failed = int(conn.execute(
                "SELECT COUNT(*) FROM upload_queue WHERE state='failed_permanent'"
            ).fetchone()[0])
    except Exception:  # noqa: BLE001
        pass

    dest_count = 0
    try:
        from arclap_station.uploaders.manager import get_manager  # noqa: PLC0415

        dest_count = len([d for d in get_manager().list() if d.enabled])
    except Exception:  # noqa: BLE001
        pass

    return {
        "name": cfg.name,
        "serial": cfg.serial,
        "site": cfg.site,
        "hostname": cfg.hostname,
        "paired": cfg.paired,
        "version": __version__,
        "uptime_seconds": int(uptime_seconds()),
        "health": {
            "overall": health_state.get("overall", "unknown"),
            "score": health_state.get("score"),
            "ran_at": health_state.get("ran_at"),
        },
        "captures_today": captures_today,
        "queue_pending": queue_pending,
        "queue_failed": queue_failed,
        "destinations_enabled": dest_count,
        "cpu_temp_c": metrics.get("cpu_temp_c"),
        "disk_used_pct": metrics.get("disk_used_pct"),
    }


@router.get("/update/check")
async def update_check(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    """Read-only: ask GitHub for the highest released tag and compare it
    to the running version. Never applies anything. Degrades gracefully
    (update_available=False, reachable=False) when GitHub is unreachable
    or rate-limited — an offline station must not error here."""
    current = __version__
    current_t = _parse_semver(current)
    result: dict[str, Any] = {
        "current": current,
        "latest": None,
        "update_available": False,
        "reachable": False,
        "releases_url": _RELEASES_URL,
    }
    try:
        import httpx  # noqa: PLC0415

        with httpx.Client(timeout=8.0) as client:
            r = client.get(_GITHUB_TAGS_URL, headers={"Accept": "application/vnd.github+json"})
            if r.status_code != 200:
                return result
            tags = r.json()
    except Exception:  # noqa: BLE001
        return result

    result["reachable"] = True
    best: tuple[int, int, int] | None = None
    best_name: str | None = None
    for t in tags if isinstance(tags, list) else []:
        name = t.get("name", "") if isinstance(t, dict) else ""
        ver = _parse_semver(name)
        if ver and (best is None or ver > best):
            best, best_name = ver, name
    if best is None:
        return result
    result["latest"] = best_name
    if current_t is not None:
        result["update_available"] = best > current_t
    return result
