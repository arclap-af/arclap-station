"""Shared FastAPI dependencies — session auth, setup-gate, IP extraction."""

from __future__ import annotations

from typing import Any

from fastapi import Cookie, Depends, HTTPException, Request, status

from arclap_station.auth import AuthManager
from arclap_station.station_config import get_station_store

SESSION_COOKIE = "arclap_session"


def get_auth() -> AuthManager:
    return AuthManager()


def get_client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


async def require_session(
    request: Request,
    arclap_session: str | None = Cookie(default=None),
    auth: AuthManager = Depends(get_auth),
) -> dict[str, Any]:
    """Reject if no valid signed session cookie is present."""
    # Setup endpoints are open until first-boot is complete (handled separately).
    sess = auth.validate_session(arclap_session)
    if sess is None:
        # also accept the Authorization: Bearer <session> form for the dev/PWA case
        authz = request.headers.get("authorization", "")
        if authz.startswith("Bearer "):
            sess = auth.validate_session(authz[7:])
    if sess is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="auth required")
    return sess


def require_first_boot() -> None:
    """Block setup endpoints after first-boot is complete."""
    station = get_station_store().load()
    auth = AuthManager()
    if station.first_boot_completed and auth.is_pin_set():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="setup already complete",
        )
