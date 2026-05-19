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
    from arclap_station.api.deps import require_ws_session  # noqa: PLC0415

    sess = await require_ws_session(ws)
    if sess is None:
        await ws.close(code=1008)
        return
    await ws.accept()
    await serve_preview_ws(ws, fps=fps)


@router.post("/reconnect")
async def reconnect(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    """Close the held PTP session and force a fresh init on next call."""
    try:
        get_adapter().close()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    audit_emit("user", "camera.reconnect", {})
    # Trigger an immediate detect so the cockpit gets fresh state.
    info = get_adapter().detect()
    return {"ok": info.detected, "model": info.model, "port": info.port}


@router.post("/sync-clock")
async def sync_clock(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    """Push the Pi's wall-clock to the camera's date/time widget."""
    from datetime import datetime  # noqa: PLC0415

    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    adapter = get_adapter()
    # Common gphoto2 paths for camera datetime — different bodies expose
    # one or the other. Try them in order; success on any wins.
    candidates = [
        "/main/settings/datetime",
        "/main/status/datetime",
        "/main/settings/datetimeutc",
    ]
    last_err: Exception | None = None
    for path in candidates:
        try:
            adapter.set_config(path, now_iso)
            audit_emit("user", "camera.sync_clock", {"path": path, "value": now_iso})
            return {"ok": True, "path": path, "value": now_iso}
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"no writable datetime widget on this camera ({last_err})",
    )


@router.post("/usb-reset")
async def usb_reset(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    """Force a USB-level reauthorize of the camera device.

    Walks /sys/bus/usb/devices looking for a Canon (04a9), Nikon (04b0),
    Sony (054c), Fujifilm (04cb) USB device and toggles its `authorized`
    sysfs flag. Recovers a stuck PTP I/O session without unplugging.
    """
    import re  # noqa: PLC0415
    from pathlib import Path as _P  # noqa: PLC0415

    vendors = {"04a9", "04b0", "054c", "04cb", "2207"}
    base = _P("/sys/bus/usb/devices")
    if not base.exists():
        raise HTTPException(status_code=503, detail="/sys/bus/usb/devices not available")
    toggled: list[str] = []
    for dev in base.iterdir():
        if not re.match(r"^\d+-\d+(\.\d+)*$", dev.name):
            continue
        vid_path = dev / "idVendor"
        auth_path = dev / "authorized"
        if not vid_path.exists() or not auth_path.exists():
            continue
        try:
            vid = vid_path.read_text().strip().lower()
        except OSError:
            continue
        if vid not in vendors:
            continue
        try:
            auth_path.write_text("0")
            import time  # noqa: PLC0415

            time.sleep(0.5)
            auth_path.write_text("1")
            toggled.append(f"{dev.name}:{vid}")
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"sysfs write denied on {dev.name}; service may need CAP_SYS_ADMIN ({exc})",
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=f"sysfs write failed: {exc}"
            ) from exc
    audit_emit("user", "camera.usb_reset", {"devices": toggled})
    return {"ok": bool(toggled), "devices": toggled}
