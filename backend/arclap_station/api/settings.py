"""Settings router: /api/settings/*."""

from __future__ import annotations

import json
import platform
import socket
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status
from pydantic import BaseModel, Field
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
    from arclap_station.api.deps import require_ws_session  # noqa: PLC0415

    sess = await require_ws_session(ws)
    if sess is None:
        await ws.close(code=1008)
        return
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


# ----- Danger Zone --------------------------------------------------------

class RestartRequest(BaseModel):
    unit: str = "arclap-station"


@router.post("/restart-service")
async def restart_service(
    payload: RestartRequest,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """Restart a systemd unit. Allowlisted to arclap-* + caddy for safety."""
    import subprocess  # noqa: PLC0415

    allowed = {
        "arclap-station",
        "arclap-station.service",
        "arclap-camera-watchdog",
        "arclap-camera-watchdog.timer",
        "arclap-retention",
        "arclap-retention.timer",
        "arclap-watchdog",
        "arclap-watchdog.timer",
        "caddy",
        "caddy.service",
        "avahi-daemon",
        "avahi-daemon.service",
    }
    if payload.unit not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unit '{payload.unit}' not in allowlist",
        )
    audit_emit("user", "system.restart_service", {"unit": payload.unit})
    # Fire-and-forget — the request that triggered the restart may itself
    # be the one we're killing. Spawn a detached subprocess.
    subprocess.Popen(
        ["systemctl", "restart", payload.unit],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"ok": True, "unit": payload.unit}


class RebootRequest(BaseModel):
    confirm_pin: str = Field(..., min_length=4, max_length=12, pattern=r"^\d+$")


@router.post("/reboot")
async def reboot(
    payload: RebootRequest,
    request: Request,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """Reboot the Pi. Requires PIN confirmation to avoid misclicks."""
    import subprocess  # noqa: PLC0415

    from arclap_station.auth import AuthManager, InvalidPin, LockedOut, PinNotSet  # noqa: PLC0415

    ip = request.client.host if request.client else "unknown"
    auth = AuthManager()
    try:
        auth.verify_pin(payload.confirm_pin, ip)
    except (LockedOut, PinNotSet) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidPin as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="PIN incorrect"
        ) from exc
    audit_emit("user", "system.reboot", {"ip": ip})
    subprocess.Popen(
        ["systemctl", "reboot"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"ok": True, "message": "reboot scheduled"}


class FactoryResetRequest(BaseModel):
    confirm_pin: str = Field(..., min_length=4, max_length=12, pattern=r"^\d+$")
    purge_photos: bool = False


@router.post("/factory-reset")
async def factory_reset(
    payload: FactoryResetRequest,
    request: Request,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """Wipe configuration and (optionally) photos. Requires PIN confirmation.

    What gets cleared:
    - /etc/arclap/auth.json (PIN)
    - /etc/arclap/station.json (station identity → resets first_boot)
    - destinations table
    - schedules table
    - audit_log table
    - upload_queue table

    What's preserved unless `purge_photos=True`:
    - /media/sdcard/photos/* (captured photos themselves)
    - photos DB rows
    """
    import shutil  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    from arclap_station.auth import AuthManager, InvalidPin, LockedOut, PinNotSet  # noqa: PLC0415

    ip = request.client.host if request.client else "unknown"
    auth = AuthManager()
    try:
        auth.verify_pin(payload.confirm_pin, ip)
    except (LockedOut, PinNotSet) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidPin as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="PIN incorrect"
        ) from exc

    audit_emit(
        "user",
        "system.factory_reset",
        {"ip": ip, "purge_photos": payload.purge_photos},
    )

    from arclap_station.config import get_settings as _gs  # noqa: PLC0415
    from arclap_station.db import get_db as _gdb  # noqa: PLC0415

    s = _gs()
    db = _gdb()
    # Wipe destination + schedule + audit + queue tables. Keep `photos`
    # intact unless purge_photos is set.
    with db.tx() as conn:
        conn.execute("DELETE FROM destinations")
        conn.execute("DELETE FROM schedules")
        conn.execute("DELETE FROM upload_queue")
        conn.execute("DELETE FROM audit_log")
        conn.execute("DELETE FROM tokens")
        if payload.purge_photos:
            conn.execute("DELETE FROM photos")
    # Delete on-disk secrets and identity.
    for p in [s.paths.etc / "auth.json", s.paths.etc / "station.json"]:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    # Optionally wipe captured photos.
    if payload.purge_photos and s.paths.photos.exists():
        try:
            shutil.rmtree(s.paths.photos, ignore_errors=True)
            s.paths.photos.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    # Schedule a service restart so the lifespan re-initializes a clean state.
    subprocess.Popen(
        ["systemctl", "restart", "arclap-station"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"ok": True, "message": "factory reset complete; service is restarting"}


__all__ = ["router", "StationConfig"]
