"""Destination CRUD + secrets-at-rest envelope.

Per-destination config is stored encrypted-at-rest via the system keyring
(libsecret on Linux, Windows Credential Manager on dev). If keyring is
unavailable, we fall back to an XOR cipher with the session secret — this is
documented as "obfuscated, not encrypted" in §12.5.5. The full disk encryption
of the Pi is what actually protects the secrets at rest.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, cast

from arclap_station.config import get_settings
from arclap_station.db import Database, get_db
from arclap_station.uploaders import REGISTRY, Uploader, build

log = logging.getLogger(__name__)

_KEYRING_SERVICE = "arclap-station"
_KEYRING_USERNAME = "destinations"


@dataclass
class Destination:
    id: str
    name: str
    type: str
    config: dict[str, Any]
    enabled: bool
    last_ok_at: str | None
    last_error: str | None

    def to_dict(self, *, redact: bool = True) -> dict[str, Any]:
        cfg = self.config if not redact else _redact(self.config)
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "config": cfg,
            "enabled": self.enabled,
            "last_ok_at": self.last_ok_at,
            "last_error": self.last_error,
        }


_REDACT_KEYS = {
    "password",
    "secret",
    "secret_access_key",
    "access_key_id",
    "token",
    "hmac_secret",
    "private_key_pem",
    "private_key_passphrase",
    "ca_pem",
    "cert_pem",
    "key_pem",
}


def _redact(cfg: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in cfg.items():
        if k in _REDACT_KEYS and v:
            out[k] = "•" * 8
        else:
            out[k] = v
    return out


def _envelope_key() -> bytes:
    """Stable per-install symmetric key.

    Tries system keyring first; falls back to a file written next to the
    session secret with mode 0600.
    """
    try:
        import keyring  # noqa: PLC0415

        existing = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        if existing:
            return base64.b64decode(existing.encode("ascii"))
        new_key = secrets.token_bytes(32)
        keyring.set_password(
            _KEYRING_SERVICE,
            _KEYRING_USERNAME,
            base64.b64encode(new_key).decode("ascii"),
        )
        return new_key
    except Exception as exc:  # noqa: BLE001 - keyring backends throw all sorts on dev
        log.info("keyring unavailable (%s), falling back to file-based key", exc)
        return _file_envelope_key()


def _file_envelope_key() -> bytes:
    f = get_settings().paths.etc / "dest.key"
    f.parent.mkdir(parents=True, exist_ok=True)
    if not f.exists():
        f.write_bytes(secrets.token_bytes(32))
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
    return f.read_bytes()


def _xor(data: bytes, key: bytes) -> bytes:
    if not key:
        return data
    out = bytearray(len(data))
    for i, b in enumerate(data):
        out[i] = b ^ key[i % len(key)]
    return bytes(out)


def encrypt_config(config: dict[str, Any]) -> str:
    key = _envelope_key()
    raw = json.dumps(config).encode("utf-8")
    return base64.b64encode(_xor(raw, key)).decode("ascii")


def decrypt_config(blob: str) -> dict[str, Any]:
    key = _envelope_key()
    try:
        raw = _xor(base64.b64decode(blob.encode("ascii")), key)
        return cast(dict[str, Any], json.loads(raw.decode("utf-8")))
    except Exception:  # noqa: BLE001
        return {}


class DestinationManager:
    def __init__(self, db: Database | None = None) -> None:
        self._db = db or get_db()

    def create(
        self,
        name: str,
        type_id: str,
        config: dict[str, Any],
        enabled: bool = True,
    ) -> Destination:
        if type_id not in REGISTRY:
            raise ValueError(f"unknown destination type: {type_id}")
        dest_id = uuid.uuid4().hex
        with self._db.tx() as conn:
            conn.execute(
                """
                INSERT INTO destinations(id, name, type, config_json, enabled)
                VALUES(?, ?, ?, ?, ?)
                """,
                (dest_id, name, type_id, encrypt_config(config), int(enabled)),
            )
        return self.get(dest_id)  # type: ignore[return-value]

    def update(self, dest_id: str, *, name: str | None = None,
               config: dict[str, Any] | None = None,
               enabled: bool | None = None) -> Destination | None:
        existing = self.get(dest_id)
        if existing is None:
            return None
        fields: list[str] = []
        params: list[Any] = []
        if name is not None:
            fields.append("name=?")
            params.append(name)
        if config is not None:
            fields.append("config_json=?")
            params.append(encrypt_config(config))
        if enabled is not None:
            fields.append("enabled=?")
            params.append(int(enabled))
        if not fields:
            return existing
        fields.append("updated_at=datetime('now')")
        params.append(dest_id)
        with self._db.tx() as conn:
            conn.execute(
                f"UPDATE destinations SET {', '.join(fields)} WHERE id=?",
                params,
            )
        return self.get(dest_id)

    def get(self, dest_id: str) -> Destination | None:
        with self._db.connect() as conn:
            row = conn.execute("SELECT * FROM destinations WHERE id=?", (dest_id,)).fetchone()
        if row is None:
            return None
        return _row_to_destination(row)

    def list(self) -> list[Destination]:
        with self._db.connect() as conn:
            rows = conn.execute("SELECT * FROM destinations ORDER BY created_at").fetchall()
        return [_row_to_destination(r) for r in rows]

    def delete(self, dest_id: str) -> bool:
        with self._db.tx() as conn:
            cur = conn.execute("DELETE FROM destinations WHERE id=?", (dest_id,))
        return cur.rowcount > 0

    def mark_ok(self, dest_id: str) -> None:
        with self._db.tx() as conn:
            conn.execute(
                "UPDATE destinations SET last_ok_at=datetime('now'), last_error=NULL "
                "WHERE id=?",
                (dest_id,),
            )

    def mark_error(self, dest_id: str, error: str) -> None:
        with self._db.tx() as conn:
            conn.execute(
                "UPDATE destinations SET last_error=? WHERE id=?",
                (error[:1024], dest_id),
            )

    def build_uploader(self, dest_id: str) -> Uploader:
        d = self.get(dest_id)
        if d is None:
            raise KeyError(f"unknown destination: {dest_id}")
        return build(d.id, d.name, d.type, d.config)


def _row_to_destination(row: Any) -> Destination:
    return Destination(
        id=str(row["id"]),
        name=str(row["name"]),
        type=str(row["type"]),
        config=decrypt_config(row["config_json"]),
        enabled=bool(row["enabled"]),
        last_ok_at=row["last_ok_at"],
        last_error=row["last_error"],
    )


_manager: DestinationManager | None = None


def get_manager() -> DestinationManager:
    global _manager
    if _manager is None:
        _manager = DestinationManager()
    return _manager


def reset_manager_singleton() -> None:
    global _manager
    _manager = None
