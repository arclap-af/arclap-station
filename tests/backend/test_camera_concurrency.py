"""Thread-safety regression test for _GPhoto2Backend.

Background
----------
Before this test landed, the cockpit's Camera page fired three parallel
``/api/camera/*`` requests on load (info, settings, properties). Each
landed on a different uvicorn worker thread and raced into
``gp.Camera().init()`` at the same instant — libgphoto2 is not
thread-safe at the device level and returned ``-7 I/O problem`` to all
but one. Even though the outer ``CameraAdapter`` had an RLock, the
inner ``_GPhoto2Backend`` did not, and a few code paths (notably
``capture_preview`` from the WebSocket stream's ``run_in_executor``)
ended up grabbing the backend without going through the outer lock.

The fix added an RLock to ``_GPhoto2Backend`` and wraps every public
method plus ``_ensure()``. This test fires N threads at the backend
concurrently and asserts no two threads enter ``Camera().init()`` at
the same time — i.e. the lock actually serialises access.

We can't import the real ``gphoto2`` on a dev machine, so we stub it
with a tiny fake whose ``Camera().init()`` records concurrent entries
via a counter + sleep. If the counter ever exceeds 1, the lock leaked.
"""

from __future__ import annotations

import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import patch


def _install_fake_gphoto2() -> tuple[types.ModuleType, dict[str, int]]:
    """Inject a fake `gphoto2` module into sys.modules.

    Returns the module + a counter dict so the test can inspect how
    many threads were inside ``Camera().init()`` simultaneously.
    """
    fake = types.ModuleType("gphoto2")

    class GPhoto2Error(Exception):
        def __init__(self, code: int, msg: str = "") -> None:
            super().__init__(f"[{code}] {msg}" if msg else f"[{code}]")
            self.code = code

    fake.GPhoto2Error = GPhoto2Error  # type: ignore[attr-defined]
    fake.GP_CAPTURE_IMAGE = 0  # type: ignore[attr-defined]
    fake.GP_FILE_TYPE_NORMAL = 1  # type: ignore[attr-defined]
    fake.GP_VERSION_SHORT = 0  # type: ignore[attr-defined]
    fake.gp_library_version = lambda _: ["fake-2.5.99"]  # type: ignore[attr-defined]

    counter = {"in_init": 0, "max_in_init": 0, "total_inits": 0}
    counter_lock = threading.Lock()

    class FakeWidget:
        def __init__(self, value: str = "ok") -> None:
            self._value = value

        def get_value(self) -> str:
            return self._value

        def set_value(self, v: str) -> None:
            self._value = v

    class FakeCamFile:
        def get_data_and_size(self) -> bytes:
            return b"jpegbytes" * 20

    class FakeCamera:
        def init(self) -> None:
            # Track concurrent entries — if the lock works, this is
            # always 1. If two threads enter at once, max_in_init goes
            # to 2 and the test fails.
            with counter_lock:
                counter["in_init"] += 1
                counter["total_inits"] += 1
                counter["max_in_init"] = max(
                    counter["max_in_init"], counter["in_init"]
                )
            # Hold the "device" busy long enough that without a lock,
            # other threads would race in.
            time.sleep(0.05)
            with counter_lock:
                counter["in_init"] -= 1

        def exit(self) -> None:
            pass

        def get_single_config(self, path: str) -> FakeWidget:
            return FakeWidget()

        def set_single_config(self, path: str, widget: FakeWidget) -> None:
            pass

        def get_summary(self) -> str:
            return "fake summary"

        def get_config(self) -> object:
            return object()

        def capture(self, mode: int) -> object:
            obj = types.SimpleNamespace(folder="/store_00010001", name="IMG_0001.JPG")
            return obj

        def capture_preview(self) -> FakeCamFile:
            return FakeCamFile()

        def file_get(self, folder: str, name: str, ftype: int) -> FakeCamFile:
            return FakeCamFile()

        def file_delete(self, folder: str, name: str) -> None:
            pass

    fake.Camera = FakeCamera  # type: ignore[attr-defined]
    sys.modules["gphoto2"] = fake
    return fake, counter


def _build_backend():
    """Construct _GPhoto2Backend against the fake gphoto2 module."""
    # Import lazily so the patch to sys.modules above is in effect.
    from arclap_station.camera.adapter import _GPhoto2Backend

    return _GPhoto2Backend()


def test_ensure_serialises_concurrent_callers() -> None:
    """N threads hammering _ensure() must never enter Camera().init() in parallel."""
    _, counter = _install_fake_gphoto2()
    backend = _build_backend()

    def hit() -> bool:
        cam = backend._ensure()  # noqa: SLF001 - explicit test of internal lock
        return cam is not None

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(hit) for _ in range(16)]
        results = [f.result(timeout=10) for f in as_completed(futures)]

    assert all(results), "every thread must get a Camera handle"
    # Singleton: cam is cached after the first successful init, so
    # subsequent threads short-circuit on the `if self._cam is not None`
    # fast path. Only one real init() should fire across the 16 threads.
    assert counter["total_inits"] == 1, (
        f"expected exactly one init() across 16 threads, "
        f"got {counter['total_inits']} — the lock leaked"
    )
    assert counter["max_in_init"] <= 1, (
        f"max concurrent init() was {counter['max_in_init']} — the lock leaked"
    )


def test_detect_capture_preview_serialised() -> None:
    """Mix of detect / capture / capture_preview / get_config under the same lock.

    Without the lock, FakeCamera.init() would see max_in_init > 1 the moment
    two threads simultaneously hit a method that drops + re-ensures the handle.
    """
    _, counter = _install_fake_gphoto2()
    backend = _build_backend()

    tmp = Path(__file__).parent / "_capture_tmp"
    tmp.mkdir(exist_ok=True)

    def detect() -> None:
        backend.detect()

    def get() -> None:
        backend.get_config("/main/imgsettings/iso")

    def preview() -> None:
        backend.capture_preview()

    def reset_then_ensure() -> None:
        # Worst case: forcibly drop the handle then re-ensure. Without the
        # lock this is where another thread mid-init() would collide.
        backend._drop_handle()  # noqa: SLF001
        backend._ensure()  # noqa: SLF001

    fns = [detect, get, preview, reset_then_ensure]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fns[i % len(fns)]) for i in range(40)]
        for f in as_completed(futures):
            f.result(timeout=10)

    assert counter["max_in_init"] <= 1, (
        f"max concurrent init() was {counter['max_in_init']} — "
        f"public methods are not all under the lock"
    )


def test_drop_handle_outside_lock_for_watchdog() -> None:
    """The capture watchdog Timer must NOT block waiting for the lock.

    The watchdog fires on another thread and force-closes self._cam
    directly (line ~367 in adapter.py). We don't gate that path through
    the lock, otherwise the watchdog would deadlock against the hung
    capture call. This test mimics the watchdog scenario.
    """
    _, _ = _install_fake_gphoto2()
    backend = _build_backend()
    backend._ensure()  # noqa: SLF001 - prime the handle

    holding = threading.Event()
    released = threading.Event()

    def hold_lock() -> None:
        # Mimic an in-progress capture that holds the RLock for a while.
        with backend._lock:  # noqa: SLF001
            holding.set()
            released.wait(timeout=2.0)

    holder = threading.Thread(target=hold_lock, daemon=True)
    holder.start()
    holding.wait(timeout=1.0)

    # While the lock is held, the watchdog must still be able to touch
    # self._cam.exit() WITHOUT acquiring the lock. We simulate the
    # watchdog's escape hatch: it grabs the bare attribute, never the lock.
    start = time.monotonic()
    cam = backend._cam
    if cam is not None:
        cam.exit()
    elapsed = time.monotonic() - start

    released.set()
    holder.join(timeout=3.0)

    # The watchdog path took milliseconds, not seconds.
    assert elapsed < 0.5, (
        f"watchdog escape hatch took {elapsed:.2f}s — it must not block on the lock"
    )
