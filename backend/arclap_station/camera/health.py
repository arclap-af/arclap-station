"""Cross-process camera health beacon.

The backend writes this file on every detect/capture/preview, marking
the camera handle's last known state. The camera-watchdog reads it
INSTEAD of running its own `gphoto2 --auto-detect` — that previous
design had two processes racing for the same USB interface, which was
itself a stability problem.

If the beacon is fresh (< STALE_AFTER_SEC) and `ok`, the watchdog
trusts it and exits without poking the camera. Only if the beacon is
stale or last-error is recent does the watchdog probe USB at all.

The watchdog also writes the `last_reset_at` field after a USB
authorize toggle, which the adapter reads to enforce its post-reset
grace window (no new PTP session for 15s after a reset).

File schema:
    {
      "ok":            bool,     // true = last op succeeded
      "last_ok_at":    ISO8601 | null,
      "last_error":    string | null,
      "last_error_at": ISO8601 | null,
      "model":         string | null,
      "last_reset_at": ISO8601 | null   // written by watchdog only
    }
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from arclap_station.config import get_settings

log = logging.getLogger(__name__)

BEACON_FILENAME = "camera_health.json"

# Beyond this age, the watchdog treats the beacon as missing and
# probes the USB device itself. Tuned so that an idle station with no
# captures for a while still gets a fresh reading on the watchdog
# timer's next fire (2 min cadence).
STALE_AFTER_SEC = 180.0


def _path() -> Path:
    return get_settings().paths.var / BEACON_FILENAME


def _read() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(payload: dict[str, Any]) -> None:
    p = _path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(p)
    except OSError as exc:
        log.debug("camera health beacon write failed: %s", exc)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ----- writer API (called by the camera adapter) -------------------------


def write_ok(model: str | None) -> None:
    """Record a successful camera op.

    Also tracks "flap" events — every transition from failure → ok is
    appended to a small ring buffer. If we observe N flips inside the
    flap window, we emit an audit alert so an operator knows the cable,
    connector, or USB bus is unstable (not the camera firmware).
    """
    payload = _read()
    was_failing = (payload.get("ok") is False)
    payload.update({
        "ok": True,
        "last_ok_at": _now_iso(),
        "model": model or payload.get("model"),
    })
    if was_failing:
        _record_flap(payload)
    _write(payload)


def write_failure(err: str) -> None:
    """Record a failed camera op."""
    payload = _read()
    payload.update({
        "ok": False,
        "last_error": err[:512] if err else None,
        "last_error_at": _now_iso(),
    })
    _write(payload)


def clear_failure() -> None:
    """Drop the recent-failure marker so the next ``_ensure()`` runs its
    full init ladder (1s/3s/10s) instead of fail-fast.

    Does NOT mark the beacon ok — only a real ``detect()`` success does
    that. Shared by the manual /camera/reconnect endpoint and the
    background auto-reconnect loop so a freshly re-plugged camera gets a
    genuine retry rather than a single instant-fail.
    """
    payload = _read()
    if not any(k in payload for k in ("last_error", "last_error_at")):
        return
    payload.pop("last_error", None)
    payload.pop("last_error_at", None)
    _write(payload)


def is_ok() -> bool:
    """Cheap, USB-free read of the beacon's last-known camera state."""
    return bool(_read().get("ok"))


# ----- flap detection ----------------------------------------------------

# More than FLAP_THRESHOLD recoveries inside FLAP_WINDOW_SEC = "flapping".
FLAP_THRESHOLD = 3
FLAP_WINDOW_SEC = 3600.0


def _record_flap(payload: dict[str, Any]) -> None:
    """Append a recovery event to the flap ring buffer.

    Emits `camera.flapping` (audit) when threshold is hit so the
    cockpit and any external monitor can surface "check cable /
    power" instead of letting the operator stare at a re-establishing
    PTP session every few minutes.
    """
    flaps: list[str] = list(payload.get("recoveries", []))
    flaps.append(_now_iso())
    # Trim entries older than the window.
    cutoff = datetime.now(UTC) - _timedelta_sec(FLAP_WINDOW_SEC)
    pruned: list[str] = []
    for ts in flaps[-32:]:  # cap memory
        try:
            if datetime.fromisoformat(ts) >= cutoff:
                pruned.append(ts)
        except (ValueError, TypeError):
            continue
    payload["recoveries"] = pruned
    if len(pruned) >= FLAP_THRESHOLD:
        try:
            from arclap_station.audit import emit as _audit  # noqa: PLC0415

            _audit(
                "system",
                "camera.flapping",
                {
                    "recoveries_in_window": len(pruned),
                    "window_sec": int(FLAP_WINDOW_SEC),
                    "first_at": pruned[0],
                    "latest_at": pruned[-1],
                },
            )
        except Exception:  # noqa: BLE001
            log.debug("camera.flapping audit emit failed", exc_info=True)
        # Reset the ring after alerting so we don't spam the audit
        # log every recovery once we're past threshold.
        payload["recoveries"] = []


def _timedelta_sec(s: float) -> timedelta:
    return timedelta(seconds=s)


def write_reset() -> None:
    """Record a USB-authorize reset event (written by the watchdog)."""
    payload = _read()
    payload.update({
        "ok": False,
        "last_reset_at": _now_iso(),
    })
    _write(payload)


# ----- reader API (called by the watchdog AND the adapter) ---------------


def read_state() -> dict[str, Any]:
    """Return the parsed beacon dict (possibly empty)."""
    return _read()


def read_last_reset_age() -> float | None:
    """Seconds since the last USB reset, or None if no reset on record."""
    s = _read().get("last_reset_at")
    if not s:
        return None
    try:
        ts = datetime.fromisoformat(s)
        return max(0.0, (datetime.now(UTC) - ts).total_seconds())
    except (ValueError, TypeError):
        return None


def beacon_age_sec() -> float | None:
    """Age (s) of the freshest write (ok OR error). None if file missing."""
    p = _path()
    try:
        return max(0.0, time.time() - p.stat().st_mtime)
    except OSError:
        return None


def is_fresh_and_ok() -> bool:
    """True if the most recent beacon write succeeded AND is recent."""
    age = beacon_age_sec()
    if age is None or age > STALE_AFTER_SEC:
        return False
    return bool(_read().get("ok"))
