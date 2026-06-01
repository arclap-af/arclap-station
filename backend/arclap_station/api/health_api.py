"""Health router: /api/health/* (authenticated, cockpit-facing).

Distinct from the unauthenticated /api/health deep-probe in main.py
(that one is for the systemd watchdog + load balancers). These
endpoints back the cockpit's Health view and the alert/heartbeat
config.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from arclap_station.api.deps import require_session
from arclap_station.audit import emit as audit_emit
from arclap_station.health import alerts as health_alerts
from arclap_station.health.selftest import run_selftest

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/selftest")
async def selftest_now(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    """Run the self-test on demand and return the full result. Also
    persists it + evaluates alert transitions so a manual run keeps the
    state beacon fresh."""
    result = run_selftest()
    try:
        health_alerts.evaluate_and_alert(result)
    except Exception:  # noqa: BLE001
        pass
    return result


@router.get("/state")
async def selftest_state(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    """Return the last persisted self-test result without re-running it
    (cheap; for polling). Falls back to a fresh run if no state yet."""
    state = health_alerts.read_state()
    if not state:
        state = run_selftest()
        try:
            health_alerts.write_state(state)
        except Exception:  # noqa: BLE001
            pass
    return state


@router.post("/heartbeat/test")
async def heartbeat_test(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    """Fire a one-off heartbeat to the configured alert webhook so the
    operator can confirm the integration works."""
    ok = health_alerts.send_heartbeat()
    audit_emit("user", "health.heartbeat_test", {"delivered": ok})
    return {"ok": ok, "configured": ok or bool(health_alerts._alert_webhook())}  # noqa: SLF001
