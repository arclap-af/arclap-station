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
    from arclap_station.camera import health as _ch  # noqa: PLC0415

    metrics = snapshot()
    # Avoid calling detect() here — when the camera is unplugged or
    # locked up, that path can stall for several seconds and turn /api/home
    # (polled every 5 s by the cockpit) into a tarpit. We trust the
    # cross-process health beacon for the boolean detected flag, and only
    # call detect() if the beacon is fresh-and-ok (so the call should be
    # near-instant on a cached handle).
    if _ch.is_fresh_and_ok():
        try:
            info = get_adapter().detect()
        except Exception:  # noqa: BLE001
            info = None
    else:
        info = None
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

    queue_stats: dict[str, float] = {
        **queue.stats(),
        "avg_upload_seconds": queue.avg_upload_seconds(),
    }

    captures_24h = photos.count_since(one_day_ago)
    disk_pct = float(metrics.get("disk_used_pct") or 0)
    # If info is None (beacon stale/error), camera is treated as not
    # detected for the status derivation. Status will be "warn" / "offline"
    # which is exactly what we want surfaced.
    cam_detected = bool(info and info.detected)
    status = _derive_status(
        cam_detected, dests, queue.pending_depth(), captures_24h, disk_pct,
        metrics.get("uptime_seconds", 0),
    )

    ups = _read_ups_safe()

    return {
        **metrics,
        "version": _version,
        "firmware": _version,
        "ip": primary_ip,
        "status": status,
        "camera": {
            "detected": cam_detected,
            "model": info.model if info else None,
            "battery": info.battery if info else None,
            "lens": info.lens if info else None,
            "port": info.port if info else None,
            "shutter_count": info.shutter_count if info else None,
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
        # UPS telemetry — null on a wired station with no HAT fitted,
        # which the cockpit renders as "no UPS". When a UPS HAT is
        # present the reader returns a real percent + on-battery flag.
        "ups_pct": ups.get("percent") if ups else None,
        "ups_status": (
            ("on battery" if ups.get("on_battery") else "mains") if ups else None
        ),
        "ts": now.isoformat(),
    }


def _read_ups_safe() -> dict[str, Any] | None:
    try:
        from arclap_station.hardware.ups import read_ups  # noqa: PLC0415

        ups = read_ups()
        return ups if ups.get("present") else None
    except Exception:  # noqa: BLE001
        return None


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
