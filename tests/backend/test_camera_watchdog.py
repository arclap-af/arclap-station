"""Camera USB watchdog probe logic.

The key regression guard (v0.9.4): the watchdog must NOT report
"recovered" when the body merely enumerates / answers auto-detect while
the backend is actually failing captures — the "detects-but-can't-
capture" wedge. Before the fix, a passing `gphoto2 --auto-detect`
masked a freshly-failing beacon and the reset ladder never engaged.
"""

from __future__ import annotations

from typing import Any

import pytest


def test_healthy_beacon_returns_zero(fresh_db: Any) -> None:
    from arclap_station.camera import health as ch  # noqa: PLC0415
    from arclap_station.watchdog.camera import CameraWatchdog  # noqa: PLC0415

    ch.write_ok("Canon EOS 5D Mark IV")  # fresh + ok
    assert CameraWatchdog().probe_once() == 0


def test_capture_failure_not_masked_by_responsive_autodetect(
    fresh_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh beacon failure + enumerated + auto-detect 'responsive' must
    still be treated as unhealthy (a strike), not 'recovered'."""
    from arclap_station.camera import health as ch  # noqa: PLC0415
    from arclap_station.watchdog.camera import CameraWatchdog  # noqa: PLC0415

    ch.write_failure("[-1] Unspecified error")  # fresh capture failure
    wd = CameraWatchdog()
    monkeypatch.setattr(wd, "_camera_enumerated", lambda: True)
    # Even if auto-detect says the body is there, captures are failing:
    monkeypatch.setattr(wd, "_gphoto_responsive", lambda *a, **k: True)

    rc = wd.probe_once()
    assert rc == 1, "a freshly-failing beacon must count as a strike, not recover"
    assert wd._load_state()["fail_count"] == 1  # noqa: SLF001


def test_stale_beacon_responsive_recovers(
    fresh_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No fresh failure + enumerated + responsive → genuinely healthy."""
    from arclap_station.watchdog.camera import CameraWatchdog  # noqa: PLC0415

    wd = CameraWatchdog()
    monkeypatch.setattr(wd, "_camera_enumerated", lambda: True)
    monkeypatch.setattr(wd, "_gphoto_responsive", lambda *a, **k: True)
    # No beacon file written → not "fresh failing" → recovered path holds.
    assert wd.probe_once() == 0


def test_no_camera_attached_returns_zero(
    fresh_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from arclap_station.camera import health as ch  # noqa: PLC0415
    from arclap_station.watchdog.camera import CameraWatchdog  # noqa: PLC0415

    ch.write_failure("[-1] gone")  # failing beacon...
    wd = CameraWatchdog()
    monkeypatch.setattr(wd, "_camera_enumerated", lambda: False)  # ...but unplugged
    # Fully disconnected → nothing to reset → clean exit (no strike loop).
    assert wd.probe_once() == 0
