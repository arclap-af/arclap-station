"""Home dashboard router: /api/home + /api/home/ws."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, WebSocket
from starlette.websockets import WebSocketDisconnect

from arclap_station.api.deps import require_session
from arclap_station.camera.adapter import get_adapter
from arclap_station.photos.store import get_store
from arclap_station.scheduler.engine import get_engine
from arclap_station.station_config import get_station_store
from arclap_station.telemetry.metrics import snapshot
from arclap_station.uploaders.manager import get_manager
from arclap_station.uploaders.queue import get_queue

router = APIRouter(prefix="/api/home", tags=["home"])


def _build_snapshot() -> dict[str, Any]:
    from arclap_station import __version__ as _version  # noqa: PLC0415

    metrics = snapshot()
    info = get_adapter().detect()
    engine = get_engine()
    photos = get_store()
    queue = get_queue()
    dests = get_manager().list()
    station = get_station_store().load()
    now = datetime.now(UTC)
    one_day_ago = now - timedelta(days=1)

    destinations_ok = sum(1 for d in dests if d.enabled and d.last_error is None)
    destinations_warn = sum(1 for d in dests if d.last_error)

    next_fire = engine.next_fire_time()
    # Real local IP via UDP-socket trick (no traffic, just route lookup).
    import socket as _socket  # noqa: PLC0415

    primary_ip = ""
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    try:
        s.connect(("1.1.1.1", 1))
        primary_ip = s.getsockname()[0]
    except OSError:
        pass
    finally:
        s.close()

    # Time deltas derived from real sources.
    next_capture_seconds: int | None = None
    if next_fire is not None:
        delta = (next_fire - now).total_seconds()
        next_capture_seconds = max(0, int(delta))
    last_ok = queue.last_ok_at()
    last_sync_seconds_ago: int | None = None
    if last_ok:
        try:
            last_ts = datetime.fromisoformat(last_ok.replace("Z", "+00:00"))
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=UTC)
            last_sync_seconds_ago = max(0, int((now - last_ts).total_seconds()))
        except ValueError:
            pass

    queue_stats = queue.stats()
    queue_stats["avg_upload_seconds"] = queue.avg_upload_seconds()

    captures_24h = photos.count_since(one_day_ago)
    disk_pct = float(metrics.get("disk_used_pct") or 0)
    status = _derive_status(
        info.detected, dests, queue.pending_depth(), captures_24h, disk_pct,
        metrics.get("uptime_seconds", 0),
    )

    return {
        **metrics,
        "version": _version,
        "firmware": _version,
        "ip": primary_ip,
        "status": status,
        "camera": {
            "detected": info.detected,
            "model": info.model,
            "battery": info.battery,
            "lens": info.lens,
            "port": info.port,
            "shutter_count": info.shutter_count,
        },
        "station": {
            "name": station.name,
            "hostname": station.hostname,
            "serial": station.serial,
            "paired": station.paired,
            "pair_token": station.pair_token,
            "first_boot_completed": station.first_boot_completed,
        },
        "scheduled_active": engine.active_count(),
        "next_fire": next_fire.isoformat() if next_fire else None,
        "next_capture_seconds": next_capture_seconds,
        "last_sync_seconds_ago": last_sync_seconds_ago,
        "captures_total": photos.count(),
        "captures_24h": captures_24h,
        "queue_depth": queue.pending_depth(),
        "queue_stats": queue_stats,
        "queue_pending": queue.pending_depth(),
        "queue_failed": queue_stats.get("failed", 0) + queue_stats.get("failed_permanent", 0),
        "destinations_ok": destinations_ok,
        "destinations_warn": destinations_warn,
        "destinations_total": len(dests),
        "ts": now.isoformat(),
    }


def _derive_status(
    camera_detected: bool,
    dests: list[Any],
    queue_pending: int,
    captures_24h: int,
    disk_pct: float,
    uptime_seconds: int,
) -> str:
    """Real station status derived from concrete signals. The cockpit's
    top-right pill (and the 8 Camera Primary Statuses §12.9) need an
    honest answer, not a hardcoded 'online'."""
    # Service has been up too briefly for telemetry to be trusted.
    if uptime_seconds < 30:
        return "warn"
    # Disk red zone.
    if disk_pct > 95:
        return "offline"
    if disk_pct > 85:
        return "warn"
    # Destinations all failing.
    if dests and all(d.last_error for d in dests if d.enabled):
        return "warn"
    # Queue stuck (200+ pending is a problem at 5-min schedule rate).
    if queue_pending > 200:
        return "warn"
    # Camera should be detected during the work day on a configured station.
    if not camera_detected:
        return "warn"
    return "online"


@router.get("")
async def home_snapshot(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    return _build_snapshot()


@router.get("/activity")
async def home_activity(
    limit: int = 25,
    _: dict[str, Any] = Depends(require_session),
) -> list[dict[str, Any]]:
    """Recent audit-log events for the dashboard's Activity panel.

    Returns the last N rows from `audit_log`, oldest first within the
    window so the UI can render them top-to-bottom as a feed.
    """
    from arclap_station.audit import recent as recent_audit  # noqa: PLC0415

    return recent_audit(limit=max(1, min(200, int(limit))))


@router.websocket("/ws")
async def home_ws(ws: WebSocket) -> None:
    # Gate BEFORE accept so unauthenticated clients can't see telemetry.
    from arclap_station.api.deps import require_ws_session  # noqa: PLC0415

    sess = await require_ws_session(ws)
    if sess is None:
        await ws.close(code=1008)  # 1008 = policy violation
        return
    await ws.accept()
    try:
        while True:
            await ws.send_text(json.dumps(_build_snapshot()))
            await asyncio.sleep(5.0)
    except WebSocketDisconnect:
        return
