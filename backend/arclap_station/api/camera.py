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
from arclap_station.config import get_settings
from arclap_station.photos.exif import extract_exif
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


def _refuse_if_disk_critical() -> None:
    """Raise 507 (Insufficient Storage) if the photo volume is critically low.

    Threshold is conservative on purpose — the retention sweep runs
    nightly and will free space, but in the meantime we'd rather drop a
    capture (audited) than fill the SD card and crash captures forever.
    """
    import shutil as _shutil  # noqa: PLC0415

    try:
        photos_root = get_settings().paths.photos
        usage = _shutil.disk_usage(photos_root)
        free_pct = (usage.free / usage.total) * 100 if usage.total > 0 else 100
    except (OSError, ValueError):
        return  # disk probe itself failed — let capture proceed
    if free_pct < 2.0:
        audit_emit(
            "system",
            "capture.refused_disk_full",
            {"free_pct": round(free_pct, 2)},
        )
        raise HTTPException(
            status_code=507,
            detail=f"Disk critically full ({free_pct:.1f}% free). Run retention sweep.",
        )


@router.post("/capture")
async def capture(
    enqueue: bool = True,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    # Disk-pressure gate: refuse to capture if the photo volume is
    # < 2% free. The retention sweep runs nightly and is reactive; the
    # capture path is the proactive defence. Without this gate, a
    # construction-site Pi that lost connectivity for weeks would
    # eventually wedge itself with an unwritable SD card.
    _refuse_if_disk_critical()

    try:
        photo_path: Path = get_adapter().capture()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    # Bake EXIF orientation into pixels + apply watermark (no-op if disabled).
    try:
        from arclap_station.photos.watermark import apply_watermark_and_rotate  # noqa: PLC0415

        apply_watermark_and_rotate(photo_path)
    except Exception:  # noqa: BLE001
        pass

    # Extract EXIF — ISO / shutter / aperture / dimensions — so the Gallery
    # can show real settings per photo instead of "—".
    exif, width, height = extract_exif(photo_path)
    record = get_store().register(photo_path, exif=exif, width=width, height=height)
    # Compute perceptual hash for dedup window even if dedup is off —
    # the hash is cheap and lets a future ?dedup=1 run retroactively.
    try:
        from arclap_station.photos.dedup import compute_dhash, store_hash  # noqa: PLC0415

        h = compute_dhash(photo_path)
        if h is not None:
            store_hash(record.id, h)
    except Exception:  # noqa: BLE001
        pass
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


@router.get("/properties")
async def properties(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    return get_adapter().list_config()


@router.get("/info")
async def camera_info(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    """Flat camera state + the ACTUAL choices the current body offers.

    The cockpit's Camera page uses this to render the mode/ISO/shutter/
    aperture chip rows — instead of guessing what the camera supports
    from a hardcoded list, we ask gphoto2 what its real options are.

    Fast-path: when the beacon shows a recent failure, we skip detect()
    and list_config() entirely so the page still renders fast (~30 ms
    instead of ~12 s) and shows `detected:false` plus the cached health
    error string. The cockpit polls this every 30 s; an unplugged camera
    must not make every poll a 12 s blocker.
    """
    from arclap_station.camera import health as _ch  # noqa: PLC0415

    adapter = get_adapter()
    tree: dict[str, Any] = {}
    info = None
    if _ch.is_fresh_and_ok():
        try:
            info = adapter.detect()
        except Exception:  # noqa: BLE001
            info = None
        if info and info.detected:
            try:
                tree = adapter.list_config()
            except Exception:  # noqa: BLE001
                tree = {}

    def widget(path: str) -> dict[str, Any]:
        w = tree.get(path, {}) if isinstance(tree, dict) else {}
        return w if isinstance(w, dict) else {}

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
        # v0.5: cross-process camera health beacon. Lets the cockpit
        # show "replug required" when the watchdog has given up and
        # surfaced a firmware lockup.
        "health": _camera_health_summary(),
    }


def _camera_health_summary() -> dict[str, Any]:
    """Summary of the cross-process health beacon for the cockpit."""
    try:
        from arclap_station.camera import health as _h  # noqa: PLC0415

        state = _h.read_state()
        return {
            "ok": bool(state.get("ok", False)),
            "last_ok_at": state.get("last_ok_at"),
            "last_error": state.get("last_error"),
            "last_error_at": state.get("last_error_at"),
            "last_reset_at": state.get("last_reset_at"),
            "beacon_age_sec": _h.beacon_age_sec(),
        }
    except Exception:  # noqa: BLE001
        return {
            "ok": False,
            "last_ok_at": None,
            "last_error": None,
            "last_error_at": None,
            "last_reset_at": None,
            "beacon_age_sec": None,
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
    """Close the held PTP session and force a fresh init on next call.

    Also clears the cross-process health beacon's recent-failure record so
    the next ``_ensure()`` invocation uses the full 3-attempt init ladder
    (1 s / 3 s / 10 s) instead of fail-fast. Without that clear, an operator
    who's just plugged the camera in would get a single instant-fail attempt
    because the beacon still records the unplugged state.
    """
    try:
        get_adapter().close()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    # Wipe the beacon's recent-failure marker so the next ensure() does the
    # full retry ladder. We DON'T mark it ok — detect() will do that if it
    # actually succeeds. Best-effort: never fail the request on a beacon error.
    try:
        import json as _json  # noqa: PLC0415

        from arclap_station.camera import health as _ch  # noqa: PLC0415

        path = _ch._path()  # noqa: SLF001
        try:
            payload = _json.loads(path.read_text())
        except (OSError, _json.JSONDecodeError):
            payload = {}
        for k in ("last_error_at", "last_error"):
            payload.pop(k, None)
        path.write_text(_json.dumps(payload, indent=2))
    except Exception:  # noqa: BLE001
        pass

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
    # If NO camera was found on the USB bus we still want to do something
    # useful — e.g. the operator just plugged in a camera but the kernel
    # hasn't re-enumerated yet. Trigger a re-scan + reload udev rules.
    rescanned = False
    if not toggled:
        import subprocess  # noqa: PLC0415

        try:
            subprocess.run(["udevadm", "trigger", "--subsystem-match=usb"],
                           capture_output=True, timeout=5)
            subprocess.run(["udevadm", "settle", "--timeout=3"],
                           capture_output=True, timeout=5)
            rescanned = True
        except (FileNotFoundError, subprocess.SubprocessError):
            pass

    # Also clear the camera health beacon's recent-failure record so the
    # adapter's fail-fast window doesn't short-circuit the very next
    # detect() call.
    try:
        import json as _json  # noqa: PLC0415

        from arclap_station.camera import health as _ch  # noqa: PLC0415

        bpath = _ch._path()  # noqa: SLF001
        try:
            payload = _json.loads(bpath.read_text())
        except (OSError, _json.JSONDecodeError):
            payload = {}
        for k in ("last_error_at", "last_error"):
            payload.pop(k, None)
        bpath.write_text(_json.dumps(payload, indent=2))
    except Exception:  # noqa: BLE001
        pass

    audit_emit("user", "camera.usb_reset", {
        "devices": toggled, "rescanned": rescanned,
    })
    return {"ok": bool(toggled) or rescanned, "devices": toggled, "rescanned": rescanned}
