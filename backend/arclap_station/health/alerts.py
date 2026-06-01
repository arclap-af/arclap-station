"""Health state persistence + proactive alerting + fleet heartbeat.

Three responsibilities:

1. State beacon — persist the last self-test result to a JSON file so
   the cockpit and the alert loop share one view, and so we can detect
   status *transitions* (ok -> bad) across runs.

2. Alerting — when the overall status degrades (ok/unknown -> warn/bad),
   POST a compact alert to the operator's configured webhook and emit
   an audit event. Recovery (-> ok) also alerts once, so a flapping
   station doesn't spam but a genuine fix is visible.

3. Heartbeat — a periodic "I'm alive + here's my summary" POST so a
   silent/dead station is detectable from the fleet side (the absence
   of a heartbeat is the signal). Independent of alerting.

All network I/O is best-effort and short-timeout: a down webhook must
never wedge the station.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_STATE_FILENAME = "health_state.json"
_ALERT_TIMEOUT = 8.0


def _state_path() -> Path:
    from arclap_station.config import get_settings  # noqa: PLC0415

    return get_settings().paths.var / _STATE_FILENAME


def read_state() -> dict[str, Any]:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(result: dict[str, Any]) -> None:
    """Persist the latest self-test result + the overall status we last
    alerted on (so transition detection survives a restart)."""
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.warning("could not persist health state: %s", exc)


def _station_summary() -> dict[str, Any]:
    """Compact identity + activity snapshot included in alerts/heartbeats.

    This is the per-station payload a fleet dashboard ingests — identity,
    running version, and headline activity. The version lets the fleet
    side spot stations that are behind on updates."""
    out: dict[str, Any] = {}
    try:
        from arclap_station import __version__  # noqa: PLC0415

        out["version"] = __version__
    except Exception:  # noqa: BLE001
        pass
    try:
        from arclap_station.station_config import get_station_store  # noqa: PLC0415

        cfg = get_station_store().load()
        out["station"] = cfg.name
        out["serial"] = cfg.serial
        out["site"] = cfg.site
    except Exception:  # noqa: BLE001
        pass
    try:
        from arclap_station.db import get_db  # noqa: PLC0415

        with get_db().connect() as conn:
            out["captures_today"] = int(conn.execute(
                "SELECT COUNT(*) FROM photos WHERE date(captured_at)=date('now')"
            ).fetchone()[0])
            out["queue_pending"] = int(conn.execute(
                "SELECT COUNT(*) FROM upload_queue WHERE state NOT IN ('ok','failed_permanent')"
            ).fetchone()[0])
    except Exception:  # noqa: BLE001
        pass
    return out


def _post(url: str, payload: dict[str, Any]) -> bool:
    """Best-effort JSON POST. Returns True on 2xx, never raises."""
    try:
        import httpx  # noqa: PLC0415

        with httpx.Client(timeout=_ALERT_TIMEOUT) as client:
            r = client.post(url, json=payload, headers={"User-Agent": "arclap-station-health/1"})
            return 200 <= r.status_code < 300
    except Exception as exc:  # noqa: BLE001
        log.info("health POST to %s failed: %s", url, exc)
        return False


def _alert_webhook() -> str | None:
    try:
        from arclap_station.station_config import get_station_store  # noqa: PLC0415

        url = getattr(get_station_store().load(), "alert_webhook", None)
        return url or None
    except Exception:  # noqa: BLE001
        return None


def evaluate_and_alert(result: dict[str, Any]) -> None:
    """Persist the result and fire an alert if the overall status changed.

    Transition rules:
      - degrade  (ok/unknown -> warn/bad): alert
      - recover  (warn/bad -> ok):         alert (one-shot "resolved")
      - same bucket:                       no alert (no spam)
    """
    prev = read_state()
    prev_overall = prev.get("overall")
    new_overall = result.get("overall")

    # Persist first so the cockpit always reflects the latest run even
    # if the alert POST is slow.
    write_state(result)

    if prev_overall == new_overall:
        return

    degraded = new_overall in ("warn", "bad") and prev_overall in ("ok", "unknown", None)
    recovered = new_overall == "ok" and prev_overall in ("warn", "bad")
    if not (degraded or recovered):
        return

    # Audit either way — the Activity feed is the always-on record even
    # if no webhook is configured.
    try:
        from arclap_station.audit import emit as audit_emit  # noqa: PLC0415

        audit_emit(
            "system",
            "health.recovered" if recovered else "health.degraded",
            {
                "from": prev_overall,
                "to": new_overall,
                "score": result.get("score"),
                "failing": [c["id"] for c in result.get("checks", []) if c.get("status") in ("warn", "bad")],
            },
        )
    except Exception:  # noqa: BLE001
        pass

    url = _alert_webhook()
    if not url:
        return
    failing = [c for c in result.get("checks", []) if c.get("status") in ("warn", "bad")]
    _post(url, {
        "type": "health_recovered" if recovered else "health_alert",
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "overall": new_overall,
        "previous": prev_overall,
        "score": result.get("score"),
        "issues": [{"id": c["id"], "label": c["label"], "status": c["status"], "detail": c["detail"]} for c in failing],
        **_station_summary(),
    })


def send_heartbeat(result: dict[str, Any] | None = None) -> bool:
    """POST a periodic alive+summary to the alert webhook. No-op if unset."""
    url = _alert_webhook()
    if not url:
        return False
    res = result or read_state()
    return _post(url, {
        "type": "heartbeat",
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "overall": res.get("overall", "unknown"),
        "score": res.get("score"),
        **_station_summary(),
    })
