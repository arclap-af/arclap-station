"""Security regression guards: secret redaction, IP spoofing, session revocation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from arclap_station.api.deps import get_client_ip
from arclap_station.uploaders.manager import (
    _REDACT_SENTINEL,
    _redact,
    _restore_redacted_secrets,
)


# ── secret redaction (leak fix) ──────────────────────────────────────

def test_form_secret_keys_are_redacted() -> None:
    cfg = {
        "access_key": "AKIAEXAMPLE",
        "secret_key": "shhh-super-secret",
        "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----",
        "authorization": "Bearer xyz",
        "host": "ftp.example.com",
        "bucket": "photos",
        "region": "eu-central-1",
    }
    red = _redact(cfg)
    # The exact key names the cockpit forms write — previously leaked.
    assert red["secret_key"] == _REDACT_SENTINEL
    assert red["private_key"] == _REDACT_SENTINEL
    assert red["access_key"] == _REDACT_SENTINEL
    assert red["authorization"] == _REDACT_SENTINEL
    # Non-secret fields pass through untouched.
    assert red["host"] == "ftp.example.com"
    assert red["bucket"] == "photos"
    assert red["region"] == "eu-central-1"


def test_restore_brings_back_sentineled_secret() -> None:
    out = _restore_redacted_secrets({"secret_key": _REDACT_SENTINEL, "host": "h"}, {"secret_key": "real"})
    assert out["secret_key"] == "real"


def test_cleared_secret_overwrites() -> None:
    # Empty string = operator explicitly cleared the field.
    out = _restore_redacted_secrets({"secret_key": ""}, {"secret_key": "real"})
    assert out["secret_key"] == ""


# ── client IP / PIN-lockout spoofing ─────────────────────────────────

def _req(peer: str, xff: str | None = None) -> Any:
    headers = {"x-forwarded-for": xff} if xff is not None else {}
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


def test_xff_ignored_from_untrusted_peer() -> None:
    # A direct LAN client (non-loopback) cannot spoof the lockout key.
    assert get_client_ip(_req("192.168.1.50", xff="1.2.3.4")) == "192.168.1.50"


def test_xff_uses_last_hop_from_trusted_proxy() -> None:
    # Behind Caddy (loopback), the real client is the LAST hop; the
    # attacker-controlled first hop ("9.9.9.9") must be ignored.
    assert get_client_ip(_req("127.0.0.1", xff="9.9.9.9, 192.168.1.50")) == "192.168.1.50"


def test_no_xff_uses_peer() -> None:
    assert get_client_ip(_req("127.0.0.1")) == "127.0.0.1"


# ── session revocation (logout / PIN change) ─────────────────────────

def test_logout_revokes_all_sessions(fresh_db: Any) -> None:
    from arclap_station.auth import AuthManager  # noqa: PLC0415

    a = AuthManager()
    a.set_pin("123456")
    tok = a.verify_pin("123456", "127.0.0.1")
    assert a.validate_session(tok) is not None
    a.revoke_all_sessions()
    assert a.validate_session(tok) is None, "token must be dead after logout"


def test_pin_change_invalidates_old_sessions(fresh_db: Any) -> None:
    from arclap_station.auth import AuthManager  # noqa: PLC0415

    a = AuthManager()
    a.set_pin("111111")
    old = a.verify_pin("111111", "127.0.0.1")
    a.set_pin("222222")
    assert a.validate_session(old) is None, "old session must die on PIN change"
    new = a.verify_pin("222222", "127.0.0.1")
    assert a.validate_session(new) is not None
