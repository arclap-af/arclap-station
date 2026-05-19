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
    return {
        **metrics,
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
            "paired": station.paired,
            "first_boot_completed": station.first_boot_completed,
        },
        "scheduled_active": engine.active_count(),
        "next_fire": next_fire.isoformat() if next_fire else None,
        "captures_total": photos.count(),
        "captures_24h": photos.count_since(one_day_ago),
        "queue_depth": queue.pending_depth(),
        "queue_stats": queue.stats(),
        "destinations_ok": destinations_ok,
        "destinations_warn": destinations_warn,
        "destinations_total": len(dests),
        "ts": now.isoformat(),
    }


@router.get("")
async def home_snapshot(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    return _build_snapshot()


@router.websocket("/ws")
async def home_ws(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            await ws.send_text(json.dumps(_build_snapshot()))
            await asyncio.sleep(5.0)
    except WebSocketDisconnect:
        return
