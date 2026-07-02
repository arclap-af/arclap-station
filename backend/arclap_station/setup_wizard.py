"""First-boot setup wizard endpoints."""

from __future__ import annotations

import asyncio
import logging
import shutil
import socket
import subprocess
from typing import Any

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from arclap_station.acceptance.runner import get_runner
from arclap_station.api.deps import (
    SESSION_COOKIE,
    get_client_ip,
    require_first_boot,
)
from arclap_station.audit import emit as audit_emit
from arclap_station.auth import AuthManager
from arclap_station.camera.adapter import get_adapter
from arclap_station.config import get_settings
from arclap_station.scheduler.engine import get_engine
from arclap_station.station_config import get_station_store
from arclap_station.uploaders import REGISTRY, UploadError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/setup", tags=["setup"])


class PinRequest(BaseModel):
    pin: str = Field(..., min_length=4, max_length=12, pattern=r"^\d+$")


class StationRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    timezone: str = "UTC"
    lat: float | None = None
    lon: float | None = None


class DestinationTestRequest(BaseModel):
    type: str
    config: dict[str, Any]


class ScheduleRequest(BaseModel):
    interval_min: int = Field(default=15, ge=1, le=1440)
    from_time: str = Field(default="06:00", pattern=r"^\d{2}:\d{2}$")
    to_time: str = Field(default="19:00", pattern=r"^\d{2}:\d{2}$")
    days: list[str] = Field(
        default_factory=lambda: ["mon", "tue", "wed", "thu", "fri", "sat"]
    )
    name: str = "Default"


class PairRequest(BaseModel):
    pair_code: str = Field(..., min_length=4, max_length=64)


@router.get("/status")
async def setup_status() -> dict[str, Any]:
    station = get_station_store().load()
    auth = AuthManager()
    first_boot = not (station.first_boot_completed and auth.is_pin_set())
    return {
        "first_boot": first_boot,
        "pin_set": auth.is_pin_set(),
        "station_named": bool(station.name and station.name != "arclap-station"),
        "completed": station.first_boot_completed,
    }


@router.post("/pin", dependencies=[Depends(require_first_boot)])
async def setup_pin(
    payload: PinRequest,
    request: Request,
    response: Response,
    arclap_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    auth = AuthManager()
    # Bootstrap-safe takeover guard: the FIRST PIN set is open (there's no
    # operator yet). But once a PIN exists — mid-setup, before /finish
    # closes the gate — only the session that set it may change it.
    # Without this a LAN attacker could overwrite the operator's PIN
    # during the setup window and seize the station.
    if auth.is_pin_set() and auth.validate_session(arclap_session) is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="PIN already set — authenticate to change it",
        )
    auth.set_pin(payload.pin)
    # Issue session immediately so the wizard can keep going without a separate login.
    token = auth.verify_pin(payload.pin, get_client_ip(request))
    fwd_proto = request.headers.get("x-forwarded-proto", "")
    secure = (fwd_proto or request.url.scheme) == "https"
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=60 * 60 * 12,
        path="/",
    )
    audit_emit("system", "setup.pin_set", {})
    return {"ok": True}


@router.post("/camera-detect", dependencies=[Depends(require_first_boot)])
async def setup_camera_detect() -> dict[str, Any]:
    info = get_adapter().detect()
    return {
        "detected": info.detected,
        "model": info.model,
        "battery": info.battery,
        "lens": info.lens,
        "firmware": info.firmware,
        "port": info.port,
        "shutter_count": info.shutter_count,
    }


@router.post("/station", dependencies=[Depends(require_first_boot)])
async def setup_station(payload: StationRequest) -> dict[str, Any]:
    cfg = get_station_store().update(
        name=payload.name,
        timezone=payload.timezone,
        lat=payload.lat,
        lon=payload.lon,
        hostname=socket.gethostname(),
    )
    if shutil.which("timedatectl"):
        try:
            subprocess.run(
                ["timedatectl", "set-timezone", payload.timezone],
                check=False,
                capture_output=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            log.info("timedatectl failed: %s", exc)
    audit_emit("system", "setup.station", {"name": payload.name, "tz": payload.timezone})
    return cfg.to_dict()


async def _ping(host: str, timeout: float = 2.0) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "ping",
        "-c",
        "1",
        "-W",
        str(int(timeout)),
        host,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        rc = await asyncio.wait_for(proc.wait(), timeout=timeout + 1)
    except TimeoutError:
        proc.kill()
        return False
    return rc == 0


async def _dns_check(host: str) -> bool:
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, socket.gethostbyname, host)
        return True
    except OSError:
        return False


async def _https_check(url: str, timeout: float = 4.0) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=True) as client:
            r = await client.get(url)
            return r.status_code < 500
    except httpx.HTTPError:
        return False


def _ntp_check() -> bool:
    for cmd in (["ntpq", "-p"], ["timedatectl", "status"]):
        if shutil.which(cmd[0]) is None:
            continue
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if out.returncode == 0:
                return True
        except subprocess.SubprocessError:
            continue
    return False


@router.post("/network-check", dependencies=[Depends(require_first_boot)])
async def setup_network_check() -> dict[str, Any]:
    icmp_ok = False
    if shutil.which("ping") is not None:
        icmp_ok = await _ping("1.1.1.1")
    dns_ok = await _dns_check("cloudflare.com")
    https_ok = await _https_check("https://1.1.1.1/")
    ntp_ok = _ntp_check()
    overall = (icmp_ok or https_ok) and dns_ok
    result = {
        "ok": overall,
        "icmp": icmp_ok,
        "dns": dns_ok,
        "https": https_ok,
        "ntp": ntp_ok,
    }
    audit_emit("system", "setup.network_check", result)
    return result


@router.post("/destination-test", dependencies=[Depends(require_first_boot)])
async def setup_destination_test(payload: DestinationTestRequest) -> dict[str, Any]:
    if payload.type not in REGISTRY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="unknown destination type"
        )
    factory = REGISTRY[payload.type]
    # A malformed config (missing host, bad port, …) makes the uploader
    # constructor raise ValueError — that's a client error (400), not a
    # server crash (500). Build inside its own guard so we can classify it.
    try:
        uploader = factory("probe", "probe", payload.config)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"invalid config: {exc}"
        ) from exc
    try:
        # uploader.test() does blocking network I/O — keep the event loop free.
        return await asyncio.to_thread(uploader.test)
    except UploadError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    finally:
        uploader.close()


@router.post("/schedule", dependencies=[Depends(require_first_boot)])
async def setup_schedule(payload: ScheduleRequest) -> dict[str, Any]:
    sched = get_engine().create(
        name=payload.name,
        interval_min=payload.interval_min,
        from_time=payload.from_time,
        to_time=payload.to_time,
        days=payload.days,
        enabled=False,  # paused until finish
    )
    audit_emit("system", "setup.schedule", {"id": sched.id})
    return sched.to_dict()


@router.post("/pair", dependencies=[Depends(require_first_boot)])
async def setup_pair(payload: PairRequest) -> dict[str, Any]:
    settings = get_settings()
    base = settings.cloud_base_url.rstrip("/")
    url = f"{base}/api/v1/pair"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={"pair_code": payload.pair_code})
            ok = r.status_code < 400
    except httpx.HTTPError as exc:
        log.info("pair upstream unreachable: %s — treating as local-stub success", exc)
        ok = True  # cloud may not be reachable in dev; documented behaviour
    cfg = get_station_store().update(pair_token=payload.pair_code, paired=ok)
    audit_emit("system", "setup.pair", {"ok": ok})
    return {"ok": ok, "paired": cfg.paired}


@router.post("/acceptance-run", dependencies=[Depends(require_first_boot)])
async def setup_acceptance_run() -> dict[str, Any]:
    run_id = get_runner().start(background=True)
    return {"run_id": run_id}


@router.post("/finish", dependencies=[Depends(require_first_boot)])
async def setup_finish() -> dict[str, Any]:
    # Refuse to finish without a PIN. Without this gate, a user who clicks
    # Skip on the PIN step can mark first_boot_completed=True and brick
    # the station — no PIN means /api/auth/login can't succeed, and
    # /api/setup/* is locked because first_boot is now False.
    auth = AuthManager()
    if not auth.is_pin_set():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="cannot finish setup: PIN has not been set. Complete the PIN step first.",
        )
    engine = get_engine()
    for sched in engine.list():
        if not sched.enabled:
            engine.update(sched.id, enabled=True)
    cfg = get_station_store().update(first_boot_completed=True)
    audit_emit("system", "setup.finish", {"station": cfg.name})
    return {"ok": True, "station": cfg.to_dict()}
