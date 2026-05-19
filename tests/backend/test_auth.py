"""PIN auth — set, verify, lockout, lockout expiry."""

from __future__ import annotations

import time

import pytest

from arclap_station.auth import AuthManager, InvalidPin, LockedOut, PinNotSet


def test_set_pin_and_verify() -> None:
    auth = AuthManager()
    assert auth.is_pin_set() is False
    auth.set_pin("123456")
    assert auth.is_pin_set() is True
    token = auth.verify_pin("123456", "127.0.0.1")
    assert token
    sess = auth.validate_session(token)
    assert sess is not None
    assert sess["ip"] == "127.0.0.1"


def test_verify_without_pin_raises() -> None:
    auth = AuthManager()
    with pytest.raises(PinNotSet):
        auth.verify_pin("0000", "127.0.0.1")


def test_invalid_pin_records_failure() -> None:
    auth = AuthManager()
    auth.set_pin("123456")
    with pytest.raises(InvalidPin):
        auth.verify_pin("999999", "10.0.0.1")
    assert auth.lockout_remaining("10.0.0.1") == 0  # 1 attempt only


def test_lockout_after_five_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = AuthManager()
    auth.set_pin("123456")
    for _ in range(5):
        with pytest.raises(InvalidPin):
            auth.verify_pin("000000", "10.0.0.2")
    with pytest.raises(LockedOut) as ei:
        auth.verify_pin("000000", "10.0.0.2")
    assert ei.value.seconds_remaining > 0


def test_lockout_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = AuthManager()
    auth.set_pin("123456")
    # Trip the lockout
    for _ in range(5):
        with pytest.raises(InvalidPin):
            auth.verify_pin("000000", "10.0.0.3")
    # Fast-forward time past the lockout window
    real_time = time.time
    now = real_time() + auth._settings.pin_lockout_seconds + 1
    monkeypatch.setattr(time, "time", lambda: now)
    # Lockout should now be expired and a fresh PIN should succeed.
    token = auth.verify_pin("123456", "10.0.0.3")
    assert token


def test_session_token_is_signed_and_validated() -> None:
    auth = AuthManager()
    auth.set_pin("987654")
    token = auth.verify_pin("987654", "1.1.1.1")
    assert auth.validate_session(token) is not None
    assert auth.validate_session(token + "tamper") is None
    assert auth.validate_session(None) is None
