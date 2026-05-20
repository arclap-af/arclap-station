"""Destination CRUD + secrets-at-rest envelope + orphan-rescue.

Per-destination config is stored encrypted-at-rest via the system keyring
(libsecret on Linux, Windows Credential Manager on dev). If keyring is
unavailable, we fall back to an XOR cipher with the session secret — this is
documented as "obfuscated, not encrypted" in §12.5.5. The full disk encryption
of the Pi is what actually protects the secrets at rest.
"""

from __future__ import annotations

import base64
import datetime as _dt
import json
import logging
import os
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, cast


def datetime_now_iso() -> str:
    """ISO-8601 UTC second-precision — matches what SQLite's datetime('now') emits."""
    return _dt.datetime.now(tz=_dt.UTC).strftime("%Y-%m-%d %H:%M:%S")

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
        """Serialise for the cockpit.

        Includes upload-queue counters and a today-bytes total so the
        Destinations cards in the cockpit stop reading "Last sync:
        never · Queue: 0 · Failed: 0" forever. Earlier versions
        emitted only the static fields below; the bridge fell back to
        zeros for everything else and the operator concluded the
        queue was broken.

        `retry_policy` + `encrypt_in_transit` round-trip through the
        config dict so an operator who set 3× retries on creation
        sees 3× on the card next time they load the page (the bridge
        previously defaulted to 3 / true with no persistence).
        """
        cfg = self.config if not redact else _redact(self.config)
        try:
            metrics = _destination_metrics(self.id)
        except Exception:  # noqa: BLE001
            # Metrics are non-critical — never let a transient DB
            # error fail the destinations list.
            metrics = {"queue_pending": 0, "queue_failed": 0, "bytes_today": 0}
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "config": cfg,
            "enabled": self.enabled,
            "last_ok_at": self.last_ok_at,
            # Alias for the cockpit which reads `last_sync` on the card.
            "last_sync": self.last_ok_at,
            "last_error": self.last_error,
            "queue_pending": metrics["queue_pending"],
            "queue_failed": metrics["queue_failed"],
            "bytes_today": metrics["bytes_today"],
            # Persisted inside the config so the cockpit's toggles
            # round-trip. Fall back to safe defaults for older rows.
            "retry_policy": int(self.config.get("retry_policy", 3)),
            "encrypt_in_transit": bool(
                self.config.get("encrypt_in_transit", True)
            ),
        }


def _destination_metrics(dest_id: str) -> dict[str, int]:
    """One round-trip to gather queue-pending / queue-failed / bytes-today."""
    db = get_db()
    with db.connect() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) FROM upload_queue WHERE dest_id=? AND state NOT IN ('ok','failed_permanent')",
            (dest_id,),
        ).fetchone()
        failed = conn.execute(
            "SELECT COUNT(*) FROM upload_queue WHERE dest_id=? AND state='failed_permanent'",
            (dest_id,),
        ).fetchone()
        # Photos uploaded today via this destination — join through
        # upload_queue → photos.bytes (file size at register time).
        # Best-effort; if the photos table doesn't have a size column
        # (older installs) we just return 0.
        try:
            bytes_today = conn.execute(
                """
                SELECT COALESCE(SUM(p.bytes), 0)
                FROM upload_queue q
                JOIN photos p ON p.id = q.photo_id
                WHERE q.dest_id=?
                  AND q.state='ok'
                  AND DATE(q.updated_at)=DATE('now')
                """,
                (dest_id,),
            ).fetchone()
            bytes_today_n = int(bytes_today[0] or 0)
        except Exception:  # noqa: BLE001
            bytes_today_n = 0
    return {
        "queue_pending": int(pending[0] or 0),
        "queue_failed": int(failed[0] or 0),
        "bytes_today": bytes_today_n,
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

# The literal string the cockpit sees in place of any secret field.
# When the operator edits a destination and saves without retyping a
# secret, the form sends THIS back; we must detect it on the update
# path and substitute the stored value to avoid clobbering credentials.
_REDACT_SENTINEL = "•" * 8


def _redact(cfg: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in cfg.items():
        if k in _REDACT_KEYS and v:
            out[k] = _REDACT_SENTINEL
        else:
            out[k] = v
    return out


def _restore_redacted_secrets(
    incoming: dict[str, Any], existing: dict[str, Any]
) -> dict[str, Any]:
    """Replace bullet-sentinel values with the existing stored secret.

    Critical data-loss prevention. When the cockpit GETs an existing
    destination, every secret comes back redacted as eight bullet
    characters. If the operator edits anything else (name, mode,
    remote_path) and clicks Save, the form re-sends ``password:
    "••••••••"`` (the literal it received). Without this check, we'd
    encrypt the bullets and persist them as the new password, breaking
    every subsequent upload — and the original credential is gone.

    Resolution rule: for any key in ``_REDACT_KEYS`` where the incoming
    value is exactly the redaction sentinel, fall back to the
    pre-existing stored value. Empty string is treated as "operator
    explicitly cleared this field" and DOES overwrite (so credentials
    can be removed).
    """
    out: dict[str, Any] = dict(incoming)
    for k in _REDACT_KEYS:
        if k in out and out[k] == _REDACT_SENTINEL:
            out[k] = existing.get(k, "")
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
        # Log this ONCE per process. The envelope key is fetched on
        # every uploader operation; logging the fallback every time
        # filled the journal with a multi-line warning per upload
        # and made the Settings → Logs view useless. The fallback
        # path is the documented behaviour on a Pi (libsecret
        # daemons need a desktop session); once is enough.
        global _LOGGED_KEYRING_FALLBACK
        if not _LOGGED_KEYRING_FALLBACK:
            log.info(
                "keyring unavailable (%s); using file-based envelope key. "
                "This is fine on a headless Pi — logged once per process.",
                exc,
            )
            _LOGGED_KEYRING_FALLBACK = True
        return _file_envelope_key()


# Sentinel — flipped True after the keyring fallback warning has been
# emitted once per process, so we don't spam the journal with it on
# every uploader call. Reset on process restart (i.e., per systemd
# unit lifecycle).
_LOGGED_KEYRING_FALLBACK = False


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


def _fernet() -> Any:
    """Build a Fernet key from the 32-byte envelope key.

    The envelope key is 32 raw bytes from keyring or `/etc/arclap/dest.key`;
    Fernet expects 32-byte url-safe-base64. We derive deterministically so
    the same envelope key always produces the same Fernet — meaning
    existing rows decrypt across restarts without re-keying.
    """
    from cryptography.fernet import Fernet  # noqa: PLC0415

    raw = _envelope_key()
    # Fernet expects exactly 32 bytes url-safe-base64-encoded.
    fkey = base64.urlsafe_b64encode(raw[:32].ljust(32, b"\x00"))
    return Fernet(fkey)


def encrypt_config(config: dict[str, Any]) -> str:
    raw = json.dumps(config).encode("utf-8")
    try:
        token = _fernet().encrypt(raw)
        # Tag with "f1:" so decrypt knows it's the new format.
        return "f1:" + token.decode("ascii")
    except Exception as exc:  # noqa: BLE001
        log.warning("Fernet encrypt failed (%s); falling back to legacy XOR", exc)
        return _xor_b64(raw)


def decrypt_config(blob: str) -> dict[str, Any]:
    # New format: "f1:<fernet-token>"
    if blob.startswith("f1:"):
        try:
            raw = _fernet().decrypt(blob[3:].encode("ascii"))
            return cast(dict[str, Any], json.loads(raw.decode("utf-8")))
        except Exception as exc:  # noqa: BLE001
            log.error("Fernet decrypt failed: %s — config will be empty", exc)
            return {}
    # Legacy XOR (pre-v0.2) — read once, callers should re-save to upgrade.
    try:
        key = _envelope_key()
        raw = _xor(base64.b64decode(blob.encode("ascii")), key)
        return cast(dict[str, Any], json.loads(raw.decode("utf-8")))
    except Exception as exc:  # noqa: BLE001
        log.error("legacy XOR decrypt failed: %s — config will be empty", exc)
        return {}


def _xor(data: bytes, key: bytes) -> bytes:
    """Legacy XOR — kept ONLY to decrypt pre-v0.2 stored configs."""
    if not key:
        return data
    out = bytearray(len(data))
    for i, b in enumerate(data):
        out[i] = b ^ key[i % len(key)]
    return bytes(out)


def _xor_b64(raw: bytes) -> str:
    return base64.b64encode(_xor(raw, _envelope_key())).decode("ascii")


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
        # Orphan rescue: every photo captured BEFORE any destination
        # existed was registered with upload_state='pending' but never
        # got a queue entry (nothing to enqueue against). Without this
        # backfill, those photos sit pending forever — operator sees
        # "Pending · 4" in the gallery, hits Refresh, nothing changes.
        # Enqueue them now against this newly-created destination (if
        # enabled). Schedules created later can also pick them up
        # via their dest_filter; this is the catch-all path for
        # photos with no destination at capture time.
        if enabled:
            self._enqueue_pending_orphans(dest_id)
        return self.get(dest_id)  # type: ignore[return-value]

    def _enqueue_pending_orphans(self, dest_id: str) -> None:
        """Queue every pending photo that has no queue entry yet against this dest.

        Uses the canonical UploadQueue.enqueue() rather than raw SQL so
        items get the correct state ('pending', not 'queued') that the
        worker's `_claim()` loop actually picks up. An earlier version
        inserted rows directly with state='queued' which the worker
        silently ignored.
        """
        try:
            with self._db.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT p.id
                    FROM photos p
                    WHERE p.upload_state IN ('pending', 'failed')
                      AND NOT EXISTS (
                        SELECT 1 FROM upload_queue q WHERE q.photo_id = p.id
                      )
                    """
                ).fetchall()
            photo_ids = [int(r[0]) for r in rows]
            if not photo_ids:
                return
            # Lazy import to avoid the manager <-> queue cycle.
            from arclap_station.uploaders.queue import get_queue  # noqa: PLC0415
            queue = get_queue()
            for pid in photo_ids:
                queue.enqueue(pid, [dest_id])
            log.info(
                "orphan-rescue: enqueued %d previously-pending photo(s) against dest %s",
                len(photo_ids),
                dest_id,
            )
            # Audit so the operator sees a clear breadcrumb in the
            # Activity feed: "system  upload.orphan_rescue  enqueued N photos".
            try:
                from arclap_station.audit import emit as _audit  # noqa: PLC0415
                _audit(
                    "system",
                    "upload.orphan_rescue",
                    {
                        "dest_id": dest_id,
                        "photo_count": len(photo_ids),
                    },
                )
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            # Never block destination creation on a rescue failure.
            log.warning("orphan rescue failed: %s", exc)

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
            # Restore any secret that arrived as the bullet sentinel —
            # the cockpit's edit form starts with redacted values, so
            # an unmodified Save would otherwise overwrite the real
            # credential with eight bullet characters.
            merged_config = _restore_redacted_secrets(config, existing.config)
            fields.append("config_json=?")
            params.append(encrypt_config(merged_config))
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
