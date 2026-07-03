"""Camera auto-reconnect: active-window detection + shared reconnect helper.

The background loop in main.py isn't exercised directly here (it's a thin
async wrapper); instead we test the pieces it composes: is_within_window,
ScheduleEngine.any_active_now, the reconnect_camera() helper, and the
beacon clear_failure/is_ok helpers.
"""

from __future__ import annotations

from datetime import datetime

from arclap_station.camera import health
from arclap_station.camera.adapter import reconnect_camera
from arclap_station.scheduler.engine import get_engine
from arclap_station.scheduler.rules import is_within_window

DOW = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def test_within_window_all_days_midday() -> None:
    now = datetime(2026, 7, 1, 12, 0)
    assert is_within_window(days_csv="", from_time="06:00", to_time="19:00", now=now) is True


def test_within_window_excludes_wrong_day() -> None:
    now = datetime(2026, 7, 1, 12, 0)
    other = ",".join(d for d in DOW if d != DOW[now.weekday()])
    assert is_within_window(days_csv=other, from_time="06:00", to_time="19:00", now=now) is False


def test_within_window_outside_time() -> None:
    now = datetime(2026, 7, 1, 3, 0)  # 03:00, before the 06:00 open
    assert is_within_window(days_csv="", from_time="06:00", to_time="19:00", now=now) is False


def test_within_window_overnight_span() -> None:
    now = datetime(2026, 7, 1, 23, 30)  # inside a 20:00 → 06:00 overnight window
    assert is_within_window(days_csv="", from_time="20:00", to_time="06:00", now=now) is True


def test_any_active_now_true_when_schedule_in_window() -> None:
    eng = get_engine()
    eng.create("all-day", 15, "00:00", "23:59", days=DOW)
    assert eng.any_active_now() is True


def test_any_active_now_false_when_only_disabled() -> None:
    eng = get_engine()
    s = eng.create("all-day", 15, "00:00", "23:59", days=DOW)
    eng.update(s.id, enabled=False)
    assert eng.any_active_now() is False


def test_any_active_now_false_outside_window() -> None:
    eng = get_engine()
    eng.create("night", 15, "02:00", "03:00", days=DOW)
    assert eng.any_active_now(now=datetime(2026, 7, 1, 12, 0)) is False


def test_reconnect_camera_recovers_with_mock() -> None:
    # The mock camera always detects; the shared reconnect path (close +
    # clear-failure + detect) must return a detected result and not raise.
    info = reconnect_camera()
    assert info.detected is True


def test_clear_failure_leaves_ok_false_until_real_success() -> None:
    health.write_failure("USB disconnect")
    assert health.is_ok() is False

    health.clear_failure()
    # The recent-failure marker is gone (so the next init runs the full
    # ladder), but the beacon is NOT marked ok — only a real detect does.
    assert "last_error" not in health._read()
    assert health.is_ok() is False

    health.write_ok("Canon EOS 5D Mark IV")
    assert health.is_ok() is True
