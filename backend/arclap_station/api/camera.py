"""Camera router: /api/camera/*."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, status
from pydantic import BaseModel

from arclap_station.api.deps import require_session
from arclap_station.audit import emit as audit_emit
from arclap_station.camera.adapter import get_adapter
from arclap_station.camera.stream import serve_preview_ws
from arclap_station.photos.store import get_store
from arclap_station.uploaders.queue import get_queue

router = APIRouter(prefix="/api/camera", tags=["camera"])


class SetSettingRequest(BaseModel):
    path: str
    value: Any


@router.post("/detect")
async def detect(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    info = get_adapter().detect()
    return {
        "detected": info.detected,
        "model": info.model,
        "port": info.port,
        "serial": info.serial,
        "battery": info.battery,
        "lens": info.lens,
        "firmware": info.firmware,
        "shutter_count": info.shutter_count,
    }


@router.get("/settings")
async def list_settings(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    try:
        return get_adapter().list_config()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.put("/settings")
async def set_setting(
    payload: SetSettingRequest,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    try:
        get_adapter().set_config(payload.path, payload.value)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    audit_emit("user", "camera.set_config", {"path": payload.path, "value": payload.value})
    return {"ok": True}


@router.post("/capture")
async def capture(
    enqueue: bool = True,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    try:
        photo_path: Path = get_adapter().capture()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    record = get_store().register(photo_path)
    audit_emit("user", "camera.capture", {"photo_id": record.id, "path": str(photo_path)})
    if enqueue:
        from arclap_station.scheduler.rules import list_destination_ids  # noqa: PLC0415

        dest_ids = list_destination_ids(None)
        if dest_ids:
            get_queue().enqueue(record.id, dest_ids)
    return record.to_dict()


@router.get("/properties")
async def properties(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    return get_adapter().list_config()


@router.websocket("/preview-ws")
async def preview_ws(ws: WebSocket, fps: int = Query(default=10, ge=2, le=15)) -> None:
    await ws.accept()
    await serve_preview_ws(ws, fps=fps)
