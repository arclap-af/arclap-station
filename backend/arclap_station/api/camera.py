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

    # Extract EXIF — ISO / shutter / aperture / dimensions — so the Gallery
    # can show real settings per photo instead of "—".
    exif, width, height = _extract_exif(photo_path)
    record = get_store().register(photo_path, exif=exif, width=width, height=height)
    audit_emit(
        "user",
        "camera.capture",
        {
            "photo_id": record.id,
            "path": str(photo_path),
            "iso": exif.get("iso") if exif else None,
            "shutter": exif.get("shutter") if exif else None,
            "aperture": exif.get("aperture") if exif else None,
        },
    )
    if enqueue:
        from arclap_station.scheduler.rules import list_destination_ids  # noqa: PLC0415

        dest_ids = list_destination_ids(None)
        if dest_ids:
            get_queue().enqueue(record.id, dest_ids)
    return record.to_dict()


def _extract_exif(path: Path) -> tuple[dict[str, Any], int | None, int | None]:
    """Best-effort EXIF read using Pillow. Returns (exif_dict, w, h).

    Failure modes (Pillow missing, file not an image, no EXIF tags) all
    return {}, None, None. Never raises — capture itself already succeeded.
    """
    try:
        from PIL import ExifTags, Image  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return {}, None, None
    try:
        with Image.open(str(path)) as img:
            w, h = img.size
            raw = getattr(img, "_getexif", lambda: None)() or {}
    except Exception:  # noqa: BLE001
        return {}, None, None
    tags = {ExifTags.TAGS.get(k, str(k)): v for k, v in raw.items()}
    out: dict[str, Any] = {}
    # ISO can show up under either tag name.
    iso = tags.get("ISOSpeedRatings") or tags.get("PhotographicSensitivity")
    if iso is not None:
        try:
            out["iso"] = int(iso if not isinstance(iso, tuple | list) else iso[0])
        except (TypeError, ValueError):
            pass
    # Shutter — ExposureTime is a Fraction-like (num, den).
    exp = tags.get("ExposureTime")
    if exp is not None:
        try:
            if isinstance(exp, tuple) and len(exp) == 2 and exp[1]:
                out["shutter"] = f"{exp[0]}/{exp[1]}" if exp[0] < exp[1] else f"{exp[0] / exp[1]:.1f}"
            elif hasattr(exp, "numerator"):
                out["shutter"] = (
                    f"{exp.numerator}/{exp.denominator}"
                    if exp.numerator < exp.denominator
                    else f"{float(exp):.1f}"
                )
            else:
                v = float(exp)
                out["shutter"] = f"1/{int(round(1 / v))}" if v < 1 else f"{v:.1f}"
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    # Aperture — FNumber is float-like.
    fno = tags.get("FNumber") or tags.get("ApertureValue")
    if fno is not None:
        try:
            if isinstance(fno, tuple) and len(fno) == 2 and fno[1]:
                f = fno[0] / fno[1]
            elif hasattr(fno, "numerator"):
                f = float(fno)
            else:
                f = float(fno)
            out["aperture"] = f"f/{f:.1f}"
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    # Camera identity (useful for the Gallery side panel).
    if tags.get("Make"):
        out["make"] = str(tags["Make"]).strip()
    if tags.get("Model"):
        out["model"] = str(tags["Model"]).strip()
    if tags.get("LensModel"):
        out["lens"] = str(tags["LensModel"]).strip()
    if tags.get("DateTimeOriginal"):
        out["taken_at"] = str(tags["DateTimeOriginal"]).strip()
    return out, w, h


@router.get("/properties")
async def properties(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    return get_adapter().list_config()


@router.get("/info")
async def camera_info(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    """Flat camera state + the ACTUAL choices the current body offers.

    The cockpit's Camera page uses this to render the mode/ISO/shutter/
    aperture chip rows — instead of guessing what the camera supports
    from a hardcoded list, we ask gphoto2 what its real options are.
    """
    adapter = get_adapter()
    try:
        info = adapter.detect()
    except Exception:  # noqa: BLE001
        info = None
    tree: dict[str, Any] = {}
    try:
        tree = adapter.list_config()
    except Exception:  # noqa: BLE001
        tree = {}

    def widget(path: str) -> dict[str, Any]:
        return tree.get(path, {}) if isinstance(tree, dict) else {}

    def value_of(*paths: str, fallback: str = "—") -> str:
        for p in paths:
            w = widget(p)
            v = w.get("value")
            if v not in (None, ""):
                return str(v)
        return fallback

    def choices_of(*paths: str) -> list[str]:
        for p in paths:
            w = widget(p)
            ch = w.get("choices")
            if isinstance(ch, list) and ch:
                return [str(c) for c in ch]
        return []

    return {
        "detected": bool(info and info.detected),
        "model": info.model if info else None,
        "lens": info.lens if info else None,
        "battery": info.battery if info else None,
        "port": info.port if info else None,
        "shutter_count": info.shutter_count if info else None,
        "values": {
            "mode": value_of(
                "/main/capturesettings/autoexposuremode",
                "/main/capturesettings/shootmode",
            ),
            "iso": value_of("/main/imgsettings/iso"),
            "shutter": value_of("/main/capturesettings/shutterspeed"),
            "aperture": value_of("/main/capturesettings/aperture"),
            "wb": value_of("/main/imgsettings/whitebalance"),
            "drive": value_of("/main/capturesettings/drivemode"),
            "quality": value_of(
                "/main/imgsettings/imageformat",
                "/main/imgsettings/imagequality",
            ),
            "focus": value_of("/main/capturesettings/focusmode"),
            "metering": value_of("/main/capturesettings/meteringmode"),
            "picture_style": value_of("/main/imgsettings/picturestyle"),
        },
        "choices": {
            "mode": choices_of(
                "/main/capturesettings/autoexposuremode",
                "/main/capturesettings/shootmode",
            ),
            "iso": choices_of("/main/imgsettings/iso"),
            "shutter": choices_of("/main/capturesettings/shutterspeed"),
            "aperture": choices_of("/main/capturesettings/aperture"),
            "wb": choices_of("/main/imgsettings/whitebalance"),
            "drive": choices_of("/main/capturesettings/drivemode"),
            "quality": choices_of(
                "/main/imgsettings/imageformat",
                "/main/imgsettings/imagequality",
            ),
            "focus": choices_of("/main/capturesettings/focusmode"),
            "metering": choices_of("/main/capturesettings/meteringmode"),
            "picture_style": choices_of("/main/imgsettings/picturestyle"),
        },
    }


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
