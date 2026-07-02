"""Capture observability + recovery.

Covers the v0.9.3 work that closed the "self-test reads green while
scheduled captures fail" gap found in the deep audit:

  * PhotoStore.latest_captured_at() — the truth signal.
  * selftest._capture_freshness() — health judged by photos landing on
    the schedule cadence, not by flapping detection.
  * _GPhoto2Backend capture-failure escalation — a body that detects but
    can't capture now runs the recovery ladder instead of silently
    missing every frame.
"""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


# ── latest_captured_at ───────────────────────────────────────────────

def test_latest_captured_at_empty(fresh_db: Any) -> None:
    from arclap_station.photos.store import get_store  # noqa: PLC0415

    assert get_store().latest_captured_at() is None


def test_latest_captured_at_returns_newest_tz_aware(fresh_db: Any, tmp_path: Path) -> None:
    from arclap_station.photos.store import get_store  # noqa: PLC0415

    store = get_store()
    older = datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
    newer = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    store.register(tmp_path / "a.jpg", size_bytes=10, captured_at=older)
    store.register(tmp_path / "b.jpg", size_bytes=10, captured_at=newer)

    got = store.latest_captured_at()
    assert got is not None
    assert got.tzinfo is not None
    assert got == newer


# ── capture-freshness truth signal ───────────────────────────────────

def _enable_schedule(interval_min: int = 10) -> None:
    from datetime import datetime as _dt, timedelta as _td  # noqa: PLC0415

    from arclap_station.db import get_db  # noqa: PLC0415

    # A ±3h window centred on now → always active, and "opened" ~180 min
    # ago, so the freshness tests depend on photo AGE not the wall-clock
    # minute the suite happens to run at.
    now = _dt.now()
    start = (now - _td(hours=3)).strftime("%H:%M")
    end = (now + _td(hours=3)).strftime("%H:%M")
    with get_db().tx() as conn:
        conn.execute(
            """INSERT INTO schedules(id, name, interval_min, from_time, to_time,
                                     days_csv, enabled, dest_filter, conditions)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            ("s1", "Test", interval_min, start, end,
             "mon,tue,wed,thu,fri,sat,sun", 1, None, None),
        )


def test_freshness_none_outside_active_window(fresh_db: Any, tmp_path: Path) -> None:
    """Off-hours regression: a stale photo must NOT alarm when now is
    outside every enabled schedule's active window (the v0.9.3 bug that
    fired a webhook every night and weekend)."""
    from datetime import datetime as _dt, timedelta as _td  # noqa: PLC0415

    from arclap_station.db import get_db  # noqa: PLC0415
    from arclap_station.health.selftest import _capture_freshness  # noqa: PLC0415
    from arclap_station.photos.store import get_store  # noqa: PLC0415

    now = _dt.now()
    start = (now + _td(hours=3)).strftime("%H:%M")   # window starts 3h from now
    end = (now + _td(hours=4)).strftime("%H:%M")
    with get_db().tx() as conn:
        conn.execute(
            "INSERT INTO schedules(id, name, interval_min, from_time, to_time, "
            "days_csv, enabled, dest_filter, conditions) "
            "VALUES('s','S',10,?,?,'mon,tue,wed,thu,fri,sat,sun',1,NULL,NULL)",
            (start, end),
        )
    # Deliberately very stale — old logic would scream 'camera bad'.
    get_store().register(
        tmp_path / "a.jpg", size_bytes=10,
        captured_at=datetime.now(UTC) - timedelta(hours=6),
    )
    assert _capture_freshness() is None


def test_freshness_none_without_schedule(fresh_db: Any, tmp_path: Path) -> None:
    """Manual-only station (no enabled schedule) → defer to the beacon."""
    from arclap_station.health.selftest import _capture_freshness  # noqa: PLC0415
    from arclap_station.photos.store import get_store  # noqa: PLC0415

    get_store().register(tmp_path / "a.jpg", size_bytes=10, captured_at=datetime.now(UTC))
    assert _capture_freshness() is None


def test_freshness_none_when_no_photos_yet(fresh_db: Any) -> None:
    """Schedule set but nothing captured yet → don't alarm a new station."""
    from arclap_station.health.selftest import _capture_freshness  # noqa: PLC0415

    _enable_schedule()
    assert _capture_freshness() is None


def test_freshness_ok_when_capturing_on_cadence(fresh_db: Any, tmp_path: Path) -> None:
    from arclap_station.health.selftest import _capture_freshness  # noqa: PLC0415
    from arclap_station.photos.store import get_store  # noqa: PLC0415

    _enable_schedule(interval_min=10)
    get_store().register(
        tmp_path / "a.jpg", size_bytes=10,
        captured_at=datetime.now(UTC) - timedelta(minutes=3),
    )
    chk = _capture_freshness()
    assert chk is not None and chk.status == "ok"


def test_freshness_warn_when_one_missed(fresh_db: Any, tmp_path: Path) -> None:
    from arclap_station.health.selftest import _capture_freshness  # noqa: PLC0415
    from arclap_station.photos.store import get_store  # noqa: PLC0415

    _enable_schedule(interval_min=10)
    get_store().register(
        tmp_path / "a.jpg", size_bytes=10,
        captured_at=datetime.now(UTC) - timedelta(minutes=15),  # >10+grace, <2*10
    )
    chk = _capture_freshness()
    assert chk is not None and chk.status == "warn"


def test_freshness_bad_when_captures_failing(fresh_db: Any, tmp_path: Path) -> None:
    """The false-green case from the audit: detection fine, captures dead."""
    from arclap_station.health.selftest import _capture_freshness, _check_camera  # noqa: PLC0415
    from arclap_station.photos.store import get_store  # noqa: PLC0415

    _enable_schedule(interval_min=10)
    get_store().register(
        tmp_path / "a.jpg", size_bytes=10,
        captured_at=datetime.now(UTC) - timedelta(minutes=40),  # >2*10+grace
    )
    chk = _capture_freshness()
    assert chk is not None and chk.status == "bad"
    # And the full camera check must surface bad even if the beacon is
    # green (cross-check overrides flapping detection).
    with patch("arclap_station.camera.health.read_state", return_value={"ok": True}):
        assert _check_camera().status == "bad"


# ── capture-failure escalation ───────────────────────────────────────

def _install_capture_failing_gphoto2() -> types.ModuleType:
    """Fake gphoto2 whose init() succeeds but capture() always fails -1."""
    fake = types.ModuleType("gphoto2")

    class GPhoto2Error(Exception):
        def __init__(self, code: int, msg: str = "") -> None:
            super().__init__(f"[{code}] {msg}" if msg else f"[{code}]")
            self.code = code

    fake.GPhoto2Error = GPhoto2Error  # type: ignore[attr-defined]
    fake.GP_CAPTURE_IMAGE = 0  # type: ignore[attr-defined]
    fake.GP_FILE_TYPE_NORMAL = 1  # type: ignore[attr-defined]

    class FakeCamera:
        def __init__(self) -> None:
            pass

        def init(self) -> None:
            return None  # detection/init always works

        def exit(self) -> None:
            pass

        def get_single_config(self, path: str) -> object:
            return types.SimpleNamespace(get_value=lambda: "ok", set_value=lambda _: None)

        def set_single_config(self, *_: object) -> None:
            pass

        def capture(self, _kind: int) -> object:
            raise GPhoto2Error(-1, "Unspecified error")  # but capture is dead

        def get_summary(self) -> str:
            return "fake"

        def get_config(self) -> object:
            return object()

    fake.Camera = FakeCamera  # type: ignore[attr-defined]
    sys.modules["gphoto2"] = fake
    return fake


def test_capture_failures_escalate_to_restart(fresh_db: Any, tmp_path: Path) -> None:
    """init() ok + capture() failing must escalate at the threshold.

    With no switchable USB hub (power_cycle_usb → False), the ladder's
    last rung is a service self-restart — proving a detect-but-can't-
    capture body no longer silently misses every frame.
    """
    _install_capture_failing_gphoto2()
    from arclap_station.camera.adapter import _GPhoto2Backend  # noqa: PLC0415

    backend = _GPhoto2Backend()
    sentinel = tmp_path / "last_camera_restart"
    exit_calls: list[int] = []

    def fake_exit(code: int) -> None:
        exit_calls.append(code)
        raise SystemExit(code)

    with (
        patch("os._exit", side_effect=fake_exit),
        patch("arclap_station.hardware.usbhub.power_cycle_usb", return_value=False),
        patch(
            "arclap_station.camera.adapter.Path",
            side_effect=lambda p: sentinel if "last_camera_restart" in str(p) else Path(p),
        ),
    ):
        # First (threshold-1) capture failures must NOT escalate.
        for i in range(backend.MAX_CONSECUTIVE_CAPTURE_FAILURES - 1):
            with pytest.raises(Exception):  # noqa: B017,PT011 - GPhoto2Error
                backend.capture(tmp_path)
            assert exit_calls == [], f"escalated too early after {i + 1} failures"

        # The Nth consecutive failure trips the recovery → self-restart.
        try:
            backend.capture(tmp_path)
        except (SystemExit, Exception):
            pass
        assert exit_calls == [1], (
            f"expected self-restart after {backend.MAX_CONSECUTIVE_CAPTURE_FAILURES} "
            f"capture failures, got {exit_calls}"
        )


def test_capture_escalation_respects_cooldown(fresh_db: Any, tmp_path: Path) -> None:
    """After escalating once, a further failure inside the cooldown must
    NOT escalate again — a broken cable can't become a restart loop."""
    _install_capture_failing_gphoto2()
    from arclap_station.camera.adapter import _GPhoto2Backend  # noqa: PLC0415

    backend = _GPhoto2Backend()
    sentinel = tmp_path / "last_camera_restart"
    exit_calls: list[int] = []

    def fake_exit(code: int) -> None:
        exit_calls.append(code)
        raise SystemExit(code)

    with (
        patch("os._exit", side_effect=fake_exit),
        patch("arclap_station.hardware.usbhub.power_cycle_usb", return_value=False),
        patch(
            "arclap_station.camera.adapter.Path",
            side_effect=lambda p: sentinel if "last_camera_restart" in str(p) else Path(p),
        ),
    ):
        for _ in range(backend.MAX_CONSECUTIVE_CAPTURE_FAILURES):
            try:
                backend.capture(tmp_path)
            except (SystemExit, Exception):
                pass
        assert exit_calls == [1]

        # Another failure immediately after — still inside the 900s
        # cooldown — must not fire a second restart.
        try:
            backend.capture(tmp_path)
        except (SystemExit, Exception):
            pass
        assert exit_calls == [1], "cooldown failed: escalated twice in quick succession"
