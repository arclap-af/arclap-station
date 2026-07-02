"""Health self-test, alert transitions, and UPS reader."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from arclap_station.health import alerts
from arclap_station.health.selftest import Check, _worst, run_selftest
from arclap_station.hardware import ups


# ── self-test aggregation ────────────────────────────────────────────

def test_worst_status_ordering() -> None:
    assert _worst(["ok", "ok"]) == "ok"
    assert _worst(["ok", "warn"]) == "warn"
    assert _worst(["ok", "warn", "bad"]) == "bad"
    assert _worst(["ok", "unknown"]) == "unknown"
    # bad beats warn beats unknown beats ok
    assert _worst(["unknown", "bad", "warn"]) == "bad"
    assert _worst([]) == "unknown"


def test_run_selftest_shape() -> None:
    """run_selftest never raises and always returns the full shape."""
    result = run_selftest()
    assert set(result.keys()) >= {"overall", "score", "ran_at", "checks"}
    assert result["overall"] in ("ok", "warn", "bad", "unknown")
    assert 0 <= result["score"] <= 100
    assert isinstance(result["checks"], list) and result["checks"]
    for c in result["checks"]:
        assert set(c.keys()) >= {"id", "label", "status", "detail"}
        assert c["status"] in ("ok", "warn", "bad", "unknown")


def test_check_to_dict() -> None:
    c = Check("x", "X", "ok", "fine", "hint")
    d = c.to_dict()
    assert d == {"id": "x", "label": "X", "status": "ok", "detail": "fine", "hint": "hint"}


# ── alert transitions ────────────────────────────────────────────────

def test_alert_fires_only_on_transition(tmp_path: Any) -> None:
    """evaluate_and_alert posts on degrade + recover, not on same-bucket."""
    state_file = tmp_path / "health_state.json"
    posts: list[dict[str, Any]] = []

    def fake_post(url: str, payload: dict[str, Any]) -> bool:
        posts.append(payload)
        return True

    with (
        patch("arclap_station.health.alerts._state_path", return_value=state_file),
        patch("arclap_station.health.alerts._alert_webhook", return_value="http://x"),
        patch("arclap_station.health.alerts._post", side_effect=fake_post),
        patch("arclap_station.health.alerts._station_summary", return_value={}),
    ):
        # ok -> ok : no alert
        alerts.evaluate_and_alert({"overall": "ok", "score": 100, "checks": []})
        alerts.evaluate_and_alert({"overall": "ok", "score": 100, "checks": []})
        assert posts == []

        # ok -> bad : degrade alert
        alerts.evaluate_and_alert({"overall": "bad", "score": 40, "checks": [
            {"id": "camera", "label": "Camera", "status": "bad", "detail": "x"},
        ]})
        assert len(posts) == 1
        assert posts[-1]["type"] == "health_alert"

        # bad -> bad : no repeat
        alerts.evaluate_and_alert({"overall": "bad", "score": 40, "checks": []})
        assert len(posts) == 1

        # bad -> ok : recover alert
        alerts.evaluate_and_alert({"overall": "ok", "score": 100, "checks": []})
        assert len(posts) == 2
        assert posts[-1]["type"] == "health_recovered"


def test_alert_fires_on_warn_to_bad_escalation(tmp_path: Any) -> None:
    """Regression: a station already at warn that goes bad MUST alert —
    the old rule only degraded from ok/unknown, so warn->bad was masked."""
    state_file = tmp_path / "health_state.json"
    posts: list[dict[str, Any]] = []

    with (
        patch("arclap_station.health.alerts._state_path", return_value=state_file),
        patch("arclap_station.health.alerts._alert_webhook", return_value="http://x"),
        patch("arclap_station.health.alerts._post", side_effect=lambda u, p: posts.append(p) or True),
        patch("arclap_station.health.alerts._station_summary", return_value={}),
    ):
        alerts.evaluate_and_alert({"overall": "warn", "score": 80, "checks": []})
        assert len(posts) == 1  # ok -> warn : degrade
        alerts.evaluate_and_alert({"overall": "bad", "score": 40, "checks": []})
        assert len(posts) == 2, "warn -> bad escalation must alert"
        assert posts[-1]["type"] == "health_alert"


def test_no_webhook_no_post_but_still_persists(tmp_path: Any) -> None:
    state_file = tmp_path / "health_state.json"
    with (
        patch("arclap_station.health.alerts._state_path", return_value=state_file),
        patch("arclap_station.health.alerts._alert_webhook", return_value=None),
    ):
        alerts.evaluate_and_alert({"overall": "bad", "score": 0, "checks": []})
        # State persisted even with no webhook.
        assert state_file.exists()
        assert alerts.read_state()["overall"] == "bad"


# ── UPS reader ───────────────────────────────────────────────────────

def test_ups_absent_on_bare_pi() -> None:
    """With no power_supply battery and no smbus, read_ups reports absent."""
    with (
        patch("arclap_station.hardware.ups._from_power_supply", return_value=None),
        patch("arclap_station.hardware.ups._from_ina219", return_value=None),
    ):
        assert ups.read_ups() == {"present": False}


def test_ups_present_from_power_supply() -> None:
    fake = {"present": True, "percent": 80, "on_battery": False, "status": "charging"}
    with patch("arclap_station.hardware.ups._from_power_supply", return_value=fake):
        res = ups.read_ups()
        assert res["present"] is True
        assert res["percent"] == 80


def test_safe_shutdown_only_when_critical() -> None:
    """maybe_safe_shutdown triggers only when on battery AND below threshold."""
    # On mains, full → no shutdown
    with patch("arclap_station.hardware.ups.read_ups",
               return_value={"present": True, "on_battery": False, "percent": 95}):
        assert ups.maybe_safe_shutdown() is False

    # On battery but above threshold → no shutdown
    with patch("arclap_station.hardware.ups.read_ups",
               return_value={"present": True, "on_battery": True, "percent": 50}):
        assert ups.maybe_safe_shutdown() is False

    # No UPS → no shutdown
    with patch("arclap_station.hardware.ups.read_ups", return_value={"present": False}):
        assert ups.maybe_safe_shutdown() is False

    # On battery + critical → shutdown fires (subprocess mocked)
    with (
        patch("arclap_station.hardware.ups.read_ups",
              return_value={"present": True, "on_battery": True, "percent": 5, "source": "test"}),
        patch("subprocess.run") as run,
    ):
        assert ups.maybe_safe_shutdown() is True
        run.assert_called_once()
        assert "poweroff" in run.call_args[0][0]
