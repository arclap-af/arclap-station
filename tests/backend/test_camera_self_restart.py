"""Stuck-camera self-restart regression test (`_GPhoto2Backend` J-band).

libgphoto2 can wedge its internal port-info cache in a long-lived
process after a USB reset or kernel re-enumeration. Every subsequent
``Camera().init()`` then returns ``-7 I/O problem`` even though
``gphoto2 --auto-detect`` from a fresh CLI process succeeds. The only
known cure is a fresh address space.

``_GPhoto2Backend`` counts consecutive ensure() failures. After
``MAX_CONSECUTIVE_INIT_FAILURES`` it calls ``os._exit(1)`` so systemd
(``Restart=always RestartSec=3``) brings the service back fresh.

These tests exercise the trigger, the success-reset, and the cooldown.
"""

from __future__ import annotations

import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import patch


def _install_failing_gphoto2(always_fail: bool = True) -> types.ModuleType:
    """Inject a fake gphoto2 whose Camera().init() always raises -7."""
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
            if always_fail:
                raise GPhoto2Error(-7, "I/O problem")
            # else: succeed silently — used for the reset test

        def exit(self) -> None:
            pass

        def get_single_config(self, path: str) -> object:
            return types.SimpleNamespace(
                get_value=lambda: "ok", set_value=lambda _: None
            )

        def set_single_config(self, *_: object) -> None:
            pass

        def get_summary(self) -> str:
            return "fake summary"

        def get_config(self) -> object:
            return object()

    fake.Camera = FakeCamera  # type: ignore[attr-defined]
    sys.modules["gphoto2"] = fake
    return fake


def _build_backend():
    from arclap_station.camera.adapter import _GPhoto2Backend

    return _GPhoto2Backend()


def test_three_consecutive_failures_trigger_os_exit(tmp_path: Path) -> None:
    """3 ensure() failures with -7 must call os._exit(1)."""
    _install_failing_gphoto2(always_fail=True)
    backend = _build_backend()

    sentinel = tmp_path / "last_camera_restart"
    exit_calls: list[int] = []

    def fake_exit(code: int) -> None:
        # Record + raise so the caller can observe + we don't actually exit.
        exit_calls.append(code)
        raise SystemExit(code)

    with (
        patch("os._exit", side_effect=fake_exit),
        patch(
            "arclap_station.camera.adapter.Path",
            side_effect=lambda p: sentinel if "last_camera_restart" in p else Path(p),
        ),
    ):
        # First two failures should NOT exit.
        for i in range(backend.MAX_CONSECUTIVE_INIT_FAILURES - 1):
            try:
                backend._ensure()  # noqa: SLF001
            except Exception:
                pass
            assert exit_calls == [], (
                f"os._exit fired after {i+1} failures — should wait until "
                f"{backend.MAX_CONSECUTIVE_INIT_FAILURES}"
            )

        # The Nth failure should trip the restart.
        try:
            backend._ensure()  # noqa: SLF001
        except SystemExit:
            pass
        except Exception:
            pass

        assert exit_calls == [1], (
            f"expected os._exit(1) after {backend.MAX_CONSECUTIVE_INIT_FAILURES} "
            f"consecutive failures, got {exit_calls}"
        )
        assert sentinel.exists(), "restart sentinel must be written before exit"


def test_successful_init_resets_failure_counter(tmp_path: Path) -> None:
    """A successful init() in the middle of failures resets the counter to 0."""
    fake = _install_failing_gphoto2(always_fail=True)
    backend = _build_backend()

    exit_calls: list[int] = []
    sentinel = tmp_path / "last_camera_restart"

    def fake_exit(code: int) -> None:
        exit_calls.append(code)
        raise SystemExit(code)

    with (
        patch("os._exit", side_effect=fake_exit),
        patch(
            "arclap_station.camera.adapter.Path",
            side_effect=lambda p: sentinel if "last_camera_restart" in p else Path(p),
        ),
    ):
        # Two failures — counter at 2.
        for _ in range(2):
            try:
                backend._ensure()  # noqa: SLF001
            except Exception:
                pass
        assert backend._consecutive_init_failures == 2  # noqa: SLF001

        # Camera comes back — flip the mock to succeed.
        fake.Camera = type(  # type: ignore[attr-defined]
            "FakeCameraOK",
            (),
            {
                "init": lambda self: None,
                "exit": lambda self: None,
                "get_single_config": lambda self, p: types.SimpleNamespace(
                    get_value=lambda: "ok", set_value=lambda _: None
                ),
                "set_single_config": lambda self, *a: None,
                "get_summary": lambda self: "ok",
                "get_config": lambda self: object(),
                "__init__": lambda self: None,
            },
        )

        # Force a fresh ensure (current cam is None still — first 2 calls failed).
        backend._ensure()  # noqa: SLF001
        assert backend._consecutive_init_failures == 0, (
            "successful init must reset the consecutive-failure counter"
        )

        # Camera goes bad again. Now we need MAX_CONSECUTIVE_INIT_FAILURES
        # MORE failures to trigger — not just 1 (which would happen if
        # the previous count had leaked across the success).
        fake.Camera = _install_failing_gphoto2(always_fail=True).Camera  # type: ignore[attr-defined]
        backend._cam = None  # invalidate the now-stale handle
        for _ in range(backend.MAX_CONSECUTIVE_INIT_FAILURES - 1):
            try:
                backend._ensure()  # noqa: SLF001
            except Exception:
                pass
        assert exit_calls == [], (
            "exit fired before fresh streak hit the threshold — counter leaked"
        )


def test_cooldown_suppresses_loop_restart(tmp_path: Path) -> None:
    """If the sentinel says we restarted recently, exit must be suppressed."""
    _install_failing_gphoto2(always_fail=True)
    backend = _build_backend()

    sentinel = tmp_path / "last_camera_restart"
    # Pretend we restarted 60 seconds ago — inside the 300s cooldown.
    sentinel.write_text(str(time.time() - 60))

    exit_calls: list[int] = []

    def fake_exit(code: int) -> None:
        exit_calls.append(code)
        raise SystemExit(code)

    with (
        patch("os._exit", side_effect=fake_exit),
        patch(
            "arclap_station.camera.adapter.Path",
            side_effect=lambda p: sentinel if "last_camera_restart" in p else Path(p),
        ),
    ):
        # Hit the threshold.
        for _ in range(backend.MAX_CONSECUTIVE_INIT_FAILURES):
            try:
                backend._ensure()  # noqa: SLF001
            except Exception:
                pass

        assert exit_calls == [], (
            "cooldown failed: os._exit fired even though sentinel is fresh"
        )
        # And the counter should have been reset so we don't try again
        # on the very next ensure().
        assert backend._consecutive_init_failures == 0


def test_cooldown_expired_allows_restart(tmp_path: Path) -> None:
    """After the cooldown expires, a stuck camera triggers a fresh restart."""
    _install_failing_gphoto2(always_fail=True)
    backend = _build_backend()

    sentinel = tmp_path / "last_camera_restart"
    # Stale sentinel from 1 hour ago — well past the 300s cooldown.
    sentinel.write_text(str(time.time() - 3600))

    exit_calls: list[int] = []

    def fake_exit(code: int) -> None:
        exit_calls.append(code)
        raise SystemExit(code)

    with (
        patch("os._exit", side_effect=fake_exit),
        patch(
            "arclap_station.camera.adapter.Path",
            side_effect=lambda p: sentinel if "last_camera_restart" in p else Path(p),
        ),
    ):
        for _ in range(backend.MAX_CONSECUTIVE_INIT_FAILURES):
            try:
                backend._ensure()  # noqa: SLF001
            except (SystemExit, Exception):
                pass

        assert exit_calls == [1], (
            f"expected os._exit(1) after cooldown expired, got {exit_calls}"
        )
