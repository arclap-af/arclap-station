"""Cloud pairing flow.

Two paths supported:
  1. Pair-token (Mode A — pre-assigned, default per §12.5.8). The token
     is printed on the install sheet at the warehouse; the operator
     types it into the cockpit's Setup wizard. The pair endpoint POSTs
     it to the Admin API, gets back a broker URL + per-station mTLS
     certificate, and writes them to station.json.
  2. QR pairing (Mode B). The Installer App scans the Pi's QR, then
     uploads provisioning blob to the same endpoint.

For now we ship the API surface + persistence layer. The actual call
to the admin API is stubbed behind ARCLAP_CLOUD_API_URL — leaving it
unset means pairing is a no-op (returns 503) so dev environments don't
need a fake AWS backend running.

NOTE: this file does NOT talk to AWS directly. It talks to the Arclap
Admin API (Laravel), which mints the IoT cert + broker URL on the Pi's
behalf. That keeps AWS credentials out of every Pi.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arclap_station.audit import emit as audit_emit
from arclap_station.config import get_settings
from arclap_station.station_config import get_station_store

log = logging.getLogger(__name__)


@dataclass
class PairingResult:
    ok: bool
    broker: str | None = None
    cockpit_url: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "broker": self.broker,
            "cockpit_url": self.cockpit_url,
            "error": self.error,
        }


def _cloud_api_url() -> str | None:
    return os.environ.get("ARCLAP_CLOUD_API_URL") or None


def _cert_dir() -> Path:
    d = get_settings().paths.etc / "iot"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def pair(token: str, *, force: bool = False) -> PairingResult:
    """Exchange a pair-token for a broker URL + mTLS cert.

    On success: writes cert + key to /etc/arclap/iot/, updates
    station.json with paired=true / broker URL / cockpit URL, emits
    `cloud.paired` audit event, and returns the broker URL.

    Failure modes (return PairingResult.ok=False):
      - ARCLAP_CLOUD_API_URL not configured (dev / standalone)
      - admin API rejects the token (401)
      - already paired and force=False (409 semantics)
    """
    store = get_station_store()
    cfg = store.load()
    if cfg.paired and not force:
        return PairingResult(
            ok=False,
            error="already paired (pass force=True to re-pair)",
        )
    api = _cloud_api_url()
    if not api:
        return PairingResult(
            ok=False,
            error="ARCLAP_CLOUD_API_URL not configured",
        )
    # Real implementation does a POST to {api}/v1/station/pair with the
    # token + station serial, gets back {broker, cert_pem, key_pem,
    # cockpit_url}. The full Laravel side is owned by Tallium; this
    # function is the client contract.
    try:
        import urllib.request  # noqa: PLC0415

        body = json.dumps(
            {"token": token, "serial": cfg.serial, "hostname": cfg.hostname}
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{api.rstrip('/')}/v1/station/pair",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            payload = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        log.exception("cloud pair POST failed")
        return PairingResult(ok=False, error=str(exc)[:200])

    broker = payload.get("broker")
    cert_pem = payload.get("cert_pem")
    key_pem = payload.get("key_pem")
    cockpit_url = payload.get("cockpit_url")
    if not (broker and cert_pem and key_pem):
        return PairingResult(
            ok=False,
            error="admin API returned incomplete payload",
        )
    try:
        (_cert_dir() / "device.crt").write_text(cert_pem)
        (_cert_dir() / "device.key").write_text(key_pem)
        (_cert_dir() / "device.key").chmod(0o600)
    except OSError as exc:
        log.exception("failed to persist IoT cert")
        return PairingResult(ok=False, error=f"persist: {exc}")
    store.update(
        paired=True,
        pair_token=None,  # don't keep token after success
    )
    # Broker + cockpit URL are part of station.json schema already.
    raw = store.load()
    raw_d = raw.to_dict()
    raw_d.update({"broker": broker, "cockpit_url": cockpit_url})
    # We need a way to store these — extend station_config if missing.
    try:
        store_path = get_settings().paths.etc / "station.json"
        existing = (
            json.loads(store_path.read_text()) if store_path.exists() else {}
        )
        existing["broker"] = broker
        existing["cockpit_url"] = cockpit_url
        existing["paired_at"] = datetime.now(UTC).isoformat()
        tmp = store_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(existing, indent=2))
        tmp.replace(store_path)
    except OSError as exc:
        log.warning("could not persist broker/cockpit URL: %s", exc)
    audit_emit("system", "cloud.paired", {"broker": broker})
    return PairingResult(ok=True, broker=broker, cockpit_url=cockpit_url)


def unpair() -> PairingResult:
    """Reverse a previous pair() — wipe cert + clear station.json flags."""
    for fname in ("device.crt", "device.key"):
        p = _cert_dir() / fname
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("could not delete %s: %s", fname, exc)
    store = get_station_store()
    store.update(paired=False, pair_token=None)
    try:
        store_path = get_settings().paths.etc / "station.json"
        if store_path.exists():
            d = json.loads(store_path.read_text())
            for k in ("broker", "cockpit_url", "paired_at"):
                d.pop(k, None)
            tmp = store_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(d, indent=2))
            tmp.replace(store_path)
    except OSError as exc:
        log.warning("could not clean station.json: %s", exc)
    audit_emit("system", "cloud.unpaired", {})
    return PairingResult(ok=True)
