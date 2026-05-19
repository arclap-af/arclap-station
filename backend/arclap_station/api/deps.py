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


async def require_ws_session(ws: Any) -> dict[str, Any] | None:
    """Authenticate a WebSocket via the session cookie BEFORE accepting.

    Returns the session payload on success, or None if the caller should
    reject (the handler should close with code 1008). FastAPI's
    Depends() doesn't work with WebSocket pre-accept reliably, so this
    is called manually from each handler — keep it small and explicit.
    """
    # Starlette parses cookies for us — much more reliable than splitting
    # the cookie header by hand (which mis-handled empty cookies and any
    # cookie containing '=' in its value).
    token: str | None = None
    try:
        cookies = ws.cookies  # dict-like
    except Exception:  # noqa: BLE001
        cookies = {}
    if cookies:
        token = cookies.get(SESSION_COOKIE)
    # Fallback: parse the Cookie header ourselves for runtimes that don't
    # populate .cookies (some test clients).
    if not token:
        cookie_header = ""
        try:
            cookie_header = ws.headers.get("cookie", "") or ""
        except Exception:  # noqa: BLE001
            pass
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith(SESSION_COOKIE + "="):
                token = part[len(SESSION_COOKIE) + 1 :]
                break
    # Also accept ?session=… on the query string (proxied apps that can't
    # set cookies cross-origin).
    if not token:
        try:
            token = ws.query_params.get("session")
        except Exception:  # noqa: BLE001
            token = None
    if not token:
        return None
    auth = AuthManager()
    return auth.validate_session(token)
