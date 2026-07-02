"""PIN authentication with bcrypt + per-IP lockout + signed-cookie sessions."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from arclap_station.config import Settings, get_settings


class AuthError(Exception):
    """Raised when authentication fails or is locked out."""


class LockedOut(AuthError):
    def __init__(self, seconds_remaining: int) -> None:
        super().__init__(f"locked out for {seconds_remaining}s")
        self.seconds_remaining = seconds_remaining


class InvalidPin(AuthError):
    pass


class PinNotSet(AuthError):
    pass


@dataclass
class FailedAttempts:
    count: int = 0
    first_at: float = 0.0  # epoch seconds


@dataclass
class AuthState:
    """On-disk auth state."""

    pin_hash: str | None = None
    failed_attempts: dict[str, FailedAttempts] = field(default_factory=dict)
    # Monotonic session generation. Every issued token embeds the epoch
    # at sign time; validate_session rejects tokens whose epoch != the
    # current stored one. Bumped on logout and PIN change → cheap
    # server-side revocation for a stateless signed-token scheme.
    session_epoch: int = 0

    def to_json(self) -> str:
        return json.dumps(
            {
                "pin_hash": self.pin_hash,
                "session_epoch": self.session_epoch,
                "failed_attempts": {
                    ip: {"count": fa.count, "first_at": fa.first_at}
                    for ip, fa in self.failed_attempts.items()
                },
            }
        )

    @classmethod
    def from_json(cls, payload: str) -> AuthState:
        data = json.loads(payload)
        fa_raw = data.get("failed_attempts") or {}
        return cls(
            pin_hash=data.get("pin_hash"),
            session_epoch=int(data.get("session_epoch", 0)),
            failed_attempts={
                ip: FailedAttempts(count=v.get("count", 0), first_at=v.get("first_at", 0.0))
                for ip, v in fa_raw.items()
            },
        )


class AuthManager:
    """Stateless façade over the auth.json file + session signer."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._signer = TimestampSigner(self._settings.session_secret())

    # ----- state I/O ----------------------------------------------------

    @property
    def _path(self) -> Path:
        return self._settings.paths.auth_file

    def _load(self) -> AuthState:
        if not self._path.exists():
            return AuthState()
        try:
            return AuthState.from_json(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return AuthState()

    def _save(self, state: AuthState) -> None:
        self._settings.paths.ensure()
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(state.to_json(), encoding="utf-8")
        os.replace(tmp, self._path)
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass  # Windows dev box

    # ----- public --------------------------------------------------------

    def is_pin_set(self) -> bool:
        return self._load().pin_hash is not None

    def set_pin(self, pin: str) -> None:
        if not pin or not pin.isdigit() or len(pin) < 4 or len(pin) > 12:
            raise InvalidPin("PIN must be 4-12 digits")
        state = self._load()
        state.pin_hash = bcrypt.hashpw(pin.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode(
            "utf-8"
        )
        state.failed_attempts = {}
        # A PIN change invalidates every previously-issued session.
        state.session_epoch = int(state.session_epoch) + 1
        self._save(state)

    def revoke_all_sessions(self) -> None:
        """Invalidate every existing session token (called on logout).

        Sessions are stateless signed tokens, so we bump a stored epoch
        that every token must match. Cheap server-side revocation; on a
        single-operator station "log out" reasonably means "log out
        everywhere". Without this, `delete_cookie` only clears the
        browser copy — a captured token stayed valid for its full 12h.
        """
        state = self._load()
        state.session_epoch = int(state.session_epoch) + 1
        self._save(state)

    def lockout_remaining(self, ip: str) -> int:
        state = self._load()
        fa = state.failed_attempts.get(ip)
        if fa is None or fa.count < self._settings.pin_lockout_max_attempts:
            return 0
        elapsed = time.time() - fa.first_at
        remaining = int(self._settings.pin_lockout_seconds - elapsed)
        return max(0, remaining)

    def verify_pin(self, pin: str, ip: str) -> str:
        """Verify PIN and return a fresh signed session token.

        Raises LockedOut / InvalidPin / PinNotSet.
        """
        remaining = self.lockout_remaining(ip)
        if remaining > 0:
            raise LockedOut(remaining)
        state = self._load()
        if state.pin_hash is None:
            raise PinNotSet("PIN has not been configured yet")
        if not pin or not bcrypt.checkpw(pin.encode("utf-8"), state.pin_hash.encode("utf-8")):
            self._record_failure(state, ip)
            raise InvalidPin("invalid PIN")
        # success — clear failures
        state.failed_attempts.pop(ip, None)
        self._save(state)
        return self._sign_session(ip)

    def _record_failure(self, state: AuthState, ip: str) -> None:
        now = time.time()
        fa = state.failed_attempts.get(ip)
        if fa is None or now - fa.first_at > self._settings.pin_lockout_seconds:
            state.failed_attempts[ip] = FailedAttempts(count=1, first_at=now)
        else:
            fa.count += 1
        self._save(state)

    # ----- session token I/O --------------------------------------------

    # Field separator inside the signed payload. We use '|' instead of ';'
    # because RFC 6265 makes ';' a separator inside the Cookie header,
    # which causes browsers to truncate the cookie value at the first ';'
    # — even when the server quoted the value in Set-Cookie. Symptom: HTTP
    # session works (curl preserves the quoted form) but WebSocket auth
    # fails because the browser-sent cookie is truncated.
    _SEP = "|"

    def _sign_session(self, ip: str) -> str:
        epoch = self._load().session_epoch
        payload = (
            f"sub=arclap{self._SEP}ip={ip}{self._SEP}"
            f"iat={int(time.time())}{self._SEP}ep={epoch}"
        )
        return self._signer.sign(payload).decode("utf-8")

    def validate_session(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        # Some clients (curl, older Starlette versions) round-trip the
        # cookie value with surrounding quotes; strip them defensively.
        token = token.strip()
        if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
            token = token[1:-1]
        try:
            data = self._signer.unsign(
                token.encode("utf-8"), max_age=self._settings.session_max_age_seconds
            )
        except (SignatureExpired, BadSignature):
            return None
        decoded = data.decode("utf-8")
        # Accept both the new '|'-delimited format and the legacy ';' one
        # so existing sessions don't all die on upgrade.
        if self._SEP in decoded:
            parts = decoded.split(self._SEP)
        else:
            parts = decoded.split(";")
        out: dict[str, Any] = {}
        for kv in parts:
            if "=" in kv:
                k, v = kv.split("=", 1)
                out[k] = v
        # Reject tokens from before the last revocation (logout / PIN
        # change). Legacy tokens have no 'ep' — accept them only while no
        # revocation has ever happened (epoch still 0), so upgrades don't
        # log everyone out spuriously.
        cur_epoch = self._load().session_epoch
        if "ep" not in out:
            if cur_epoch != 0:
                return None
        else:
            try:
                if int(out["ep"]) != cur_epoch:
                    return None
            except (ValueError, TypeError):
                return None
        return out
