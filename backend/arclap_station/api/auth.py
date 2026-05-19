"""Auth router: /api/auth/*."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from arclap_station.api.deps import SESSION_COOKIE, get_auth, get_client_ip
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
async def logout(response: Response, request: Request) -> dict[str, Any]:
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
    return StatusResponse(
        logged_in=bool(sess),
        pin_set=auth.is_pin_set(),
        lockout_seconds_remaining=auth.lockout_remaining(ip),
    )
