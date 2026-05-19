"""Settings router: /api/settings/*."""

from __future__ import annotations

import json
import platform
import socket
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, status
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

from arclap_station.api.deps import require_session
from arclap_station.audit import emit as audit_emit
from arclap_station.audit import recent as recent_audit
from arclap_station.audit import verify_chain
from arclap_station.config import get_settings
from arclap_station.station_config import StationConfig, get_station_store
from arclap_station.telemetry.logs import follow_journal
from arclap_station.telemetry.metrics import snapshot
from arclap_station.terminal.pty import info as pty_info

router = APIRouter(prefix="/api/settings", tags=["settings"])


class GeneralUpdateRequest(BaseModel):
    name: str | None = None
    timezone: str | None = None
    lat: float | None = None
    lon: float | None = None


@router.get("/general")
async def get_general(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    cfg = get_station_store().load()
    return cfg.to_dict()


@router.put("/general")
async def update_general(
    payload: GeneralUpdateRequest,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    fields = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    cfg = get_station_store().update(**fields)
    audit_emit("user", "settings.general.update", fields)
    return cfg.to_dict()


@router.get("/network")
async def network_info(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    hostname = socket.gethostname()
    try:
        ip = socket.gethostbyname(hostname)
    except OSError:
        ip = ""
    return {
        "hostname": hostname,
        "ip": ip,
        "platform": platform.platform(),
        "python": platform.python_version(),
    }


@router.get("/security")
async def security_info(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    chain = verify_chain()
    return {
        "audit_chain": chain,
        "pty": pty_info(),
    }


@router.get("/storage")
async def storage_info(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    settings = get_settings()
    snap = snapshot()
    return {
        "photos_root": str(settings.paths.photos),
        "thumb_root": str(settings.paths.thumbnails),
        "disk_used_pct": snap["disk_used_pct"],
    }


@router.get("/system")
async def system_info(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    return {
        "version": "0.1.0",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "snapshot": snapshot(),
    }


@router.get("/audit/recent")
async def audit_recent(
    limit: int = 100,
    _: dict[str, Any] = Depends(require_session),
) -> list[dict[str, Any]]:
    return recent_audit(limit=limit)


@router.websocket("/logs-ws")
async def logs_ws(ws: WebSocket) -> None:
    await ws.accept()
    try:
        async for line in follow_journal():
            await ws.send_text(json.dumps(line))
    except WebSocketDisconnect:
        return
    except Exception:  # noqa: BLE001
        try:
            await ws.close(code=1011)
        except Exception:  # noqa: BLE001
            pass


@router.post("/pair")
async def pair_now(
    payload: dict[str, str],
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    code = payload.get("pair_code", "").strip()
    if not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="pair_code required")
    cfg = get_station_store().update(pair_token=code, paired=True)
    audit_emit("user", "cloud.pair", {"paired": True})
    return cfg.to_dict()


@router.post("/unpair")
async def unpair(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    cfg = get_station_store().update(pair_token=None, paired=False)
    audit_emit("user", "cloud.unpair", {})
    return cfg.to_dict()


__all__ = ["router", "StationConfig"]
