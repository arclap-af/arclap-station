"""Auth router: /api/auth/*."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from arclap_station.api.deps import SESSION_COOKIE, get_auth, get_client_ip, require_session
from arclap_station.audit import emit as audit_emit
from arclap_station.auth import AuthManager, InvalidPin, LockedOut, PinNotSet

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    pin: str = Field(..., min_length=4, max_length=12, pattern=r"^\d+$")


class LoginResponse(BaseModel):
    ok: bool
    session: str


class StatusResponse(BaseModel):
    logged_in: bool
    pin_set: bool
    lockout_seconds_remaining: int
    # v0.8: surface PIN age so the cockpit can nag the operator to rotate.
    pin_age_days: int | None = None
    pin_rotation_overdue: bool = False


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    auth: AuthManager = Depends(get_auth),
) -> LoginResponse:
    ip = get_client_ip(request)
    try:
        token = auth.verify_pin(payload.pin, ip)
    except LockedOut as exc:
        audit_emit("user", "auth.locked_out", {"ip": ip, "seconds": exc.seconds_remaining})
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"locked out; retry in {exc.seconds_remaining}s",
            headers={"Retry-After": str(exc.seconds_remaining)},
        ) from exc
    except PinNotSet as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="pin not set") from exc
    except InvalidPin as exc:
        audit_emit("user", "auth.invalid_pin", {"ip": ip})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid PIN") from exc
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=_secure_cookie(request),
        samesite="strict",
        max_age=60 * 60 * 12,
        path="/",
    )
    audit_emit("user", "auth.login", {"ip": ip})
    return LoginResponse(ok=True, session=token)


def _secure_cookie(request: Request) -> bool:
    """Set Secure unless we're clearly running over http (tests / dev)."""
    fwd_proto = request.headers.get("x-forwarded-proto", "")
    if fwd_proto:
        return fwd_proto == "https"
    return request.url.scheme == "https"


@router.post("/logout")
async def logout(
    response: Response,
    request: Request,
    auth: AuthManager = Depends(get_auth),
) -> dict[str, Any]:
    # Actually invalidate the session server-side, not just clear the
    # browser cookie — a captured token was otherwise valid for 12h.
    auth.revoke_all_sessions()
    response.delete_cookie(SESSION_COOKIE, path="/")
    audit_emit("user", "auth.logout", {"ip": get_client_ip(request)})
    return {"ok": True}


@router.get("/status", response_model=StatusResponse)
async def auth_status(
    request: Request,
    auth: AuthManager = Depends(get_auth),
) -> StatusResponse:
    ip = get_client_ip(request)
    cookie = request.cookies.get(SESSION_COOKIE)
    sess = auth.validate_session(cookie)
    pin_age = _pin_age_days_from_disk()
    rotation_overdue = pin_age is not None and pin_age > 90
    return StatusResponse(
        logged_in=bool(sess),
        pin_set=auth.is_pin_set(),
        lockout_seconds_remaining=auth.lockout_remaining(ip),
        pin_age_days=pin_age,
        pin_rotation_overdue=rotation_overdue,
    )


def _pin_age_days_from_disk() -> int | None:
    """Days since the auth.json file was last mtime-touched.

    A change-pin call writes a new file so mtime tracks PIN-set events.
    Returns None if the file's missing (no PIN set yet)."""
    import os as _os  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    from arclap_station.config import get_settings  # noqa: PLC0415

    p = get_settings().paths.etc / "auth.json"
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    age_days = max(0, int((_time.time() - mtime) / 86400))
    return age_days


class ChangePinRequest(BaseModel):
    current_pin: str = Field(..., min_length=4, max_length=12, pattern=r"^\d+$")
    new_pin: str = Field(..., min_length=4, max_length=12, pattern=r"^\d+$")


@router.post("/change-pin")
async def change_pin(
    payload: ChangePinRequest,
    request: Request,
    response: Response,
    auth: AuthManager = Depends(get_auth),
    sess: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """Rotate the PIN.

    Requires a valid session AND the current PIN as proof-of-possession.
    On success, the session cookie is refreshed with a new token so the
    user stays logged in.
    """
    ip = get_client_ip(request)
    # Verify the current PIN (this also handles lockout).
    try:
        auth.verify_pin(payload.current_pin, ip)
    except LockedOut as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"locked out; retry in {exc.seconds_remaining}s",
        ) from exc
    except PinNotSet as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="pin not set") from exc
    except InvalidPin as exc:
        audit_emit("user", "auth.change_pin_failed", {"ip": ip})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="current PIN incorrect"
        ) from exc
    # Reject identity rotations (no-op).
    if payload.current_pin == payload.new_pin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="new PIN must differ from the current one",
        )
    auth.set_pin(payload.new_pin)
    # Issue a fresh session so the cookie stays valid after rotation.
    token = auth.verify_pin(payload.new_pin, ip)
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
    audit_emit("user", "auth.pin_changed", {"ip": ip})
    return {"ok": True}
