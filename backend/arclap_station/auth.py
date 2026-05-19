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

    def to_json(self) -> str:
        return json.dumps(
            {
                "pin_hash": self.pin_hash,
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

    def _sign_session(self, ip: str) -> str:
        payload = f"sub=arclap;ip={ip};iat={int(time.time())}"
        return self._signer.sign(payload).decode("utf-8")

    def validate_session(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        try:
            data = self._signer.unsign(
                token.encode("utf-8"), max_age=self._settings.session_max_age_seconds
            )
        except (SignatureExpired, BadSignature):
            return None
        out: dict[str, Any] = {}
        for kv in data.decode("utf-8").split(";"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                out[k] = v
        return out
