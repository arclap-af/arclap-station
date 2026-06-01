"""Native python-gphoto2 camera adapter.

The PyPI package is `gphoto2` (https://pypi.org/project/gphoto2/), which wraps
libgphoto2. We hold a lock around the underlying handle because libgphoto2
takes exclusive ownership of the USB device.

If `gphoto2` is not importable (dev machines without libgphoto2) or
ARCLAP_MOCK_CAMERA=1 is set, this module falls back to the MockCamera in
arclap_station.camera.mock. That keeps the test suite portable.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from arclap_station.config import get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CameraInfo:
    detected: bool
    model: str | None = None
    port: str | None = None
    serial: str | None = None
    battery: str | None = None
    lens: str | None = None
    firmware: str | None = None
    shutter_count: int | None = None
    summary: str | None = None


@runtime_checkable
class CameraBackend(Protocol):
    """The minimal contract every backend (real / mock) honours."""

    def detect(self) -> CameraInfo: ...
    def get_config(self, path: str) -> Any: ...
    def set_config(self, path: str, value: Any) -> None: ...
    def list_config(self) -> dict[str, Any]: ...
    def capture(self, dest_dir: Path) -> Path: ...
    def capture_preview(self) -> bytes: ...
    def close(self) -> None: ...


class CameraAdapter:
    """Thread-safe public adapter."""

    def __init__(self, backend: CameraBackend) -> None:
        self._backend = backend
        self._lock = threading.RLock()

    @property
    def backend_name(self) -> str:
        return type(self._backend).__name__

    def detect(self) -> CameraInfo:
        with self._lock:
            return self._backend.detect()

    def get_config(self, path: str) -> Any:
        with self._lock:
            return self._backend.get_config(path)

    def set_config(self, path: str, value: Any) -> None:
        with self._lock:
            self._backend.set_config(path, value)

    def list_config(self) -> dict[str, Any]:
        with self._lock:
            return self._backend.list_config()

    # NOTE: a previous version wrapped capture/preview in a
    # ThreadPoolExecutor to enforce a timeout. That broke real captures
    # because libgphoto2's Camera handle is bound to the thread that
    # called .init() — calling .capture() from a worker thread produced
    # "-1 Unspecified error" because the USB context wasn't shared.
    # We're back to in-thread capture under the RLock. The
    # camera-watchdog timer + USB reset (every 2 min) handles the
    # hang-recovery case at a coarser granularity instead.

    def capture(self, dest_dir: Path | None = None) -> Path:
        with self._lock:
            target = dest_dir or self._default_capture_dir()
            target.mkdir(parents=True, exist_ok=True)
            return self._backend.capture(target)

    def capture_preview(self) -> bytes:
        with self._lock:
            return self._backend.capture_preview()

    def close(self) -> None:
        with self._lock:
            self._backend.close()

    def keepalive(self) -> bool:
        """Poke the camera to keep its PTP session warm between captures.
        No-op (returns False) for backends that don't implement it
        (e.g. the mock)."""
        fn = getattr(self._backend, "keepalive", None)
        if fn is None:
            return False
        with self._lock:
            return bool(fn())

    @staticmethod
    def _default_capture_dir() -> Path:
        now = datetime.now(UTC)
        root = get_settings().paths.photos
        return root / f"{now.year:04d}" / f"{now.month:02d}" / f"{now.day:02d}"


class _GPhoto2Backend:
    """Real backend using python-gphoto2.

    Stability defences layered in v0.5 (see CHANGELOG):
      B  After init we disable the camera's auto-power-off and set
         capture-target to internal RAM (eliminates card-related fails).
      C  init() is retried up to 3 times with 1s/3s/10s backoff so a
         transient EBUSY (e.g. kernel still tearing down a previous
         handle) doesn't permanently break the singleton.
      D  Before every capture we issue a cheap GET to wake the camera
         and verify the handle is live. One retry on transient I/O error.
      E  capture-target = SDRAM: we never touch the camera's CF/SD card,
         so card-full / write-protect / slow-card stop manifesting as
         capture failures.
      I  Capture is wrapped in a watchdog `threading.Timer` that
         force-closes the handle if the call exceeds CAPTURE_TIMEOUT.
         Closing the handle from another thread is safe in libgphoto2;
         calling capture() from another thread is not, so we don't.

    Health beacon (F): every successful detect/capture/preview updates a
    JSON file the watchdog reads instead of running its own gphoto2
    probe — so two processes never fight for the USB interface.
    """

    # Total wallclock budget for a single capture before we force-close
    # the handle. The Canon 5D MkIV finishes a JPEG capture in ~1.5s
    # under good conditions; 45s catches any pathological hang.
    CAPTURE_TIMEOUT_SEC = 45.0
    # How long after a USB reset to skip self-init (let the kernel
    # finish re-enumerating before we slam in a new PTP session).
    POST_RESET_GRACE_SEC = 15.0
    # J: stuck-libgphoto2 self-restart. After this many CONSECUTIVE final
    # ensure() failures we self-terminate so systemd (Restart=always)
    # brings us back with a fresh address space — the only known cure
    # when libgphoto2's internal port-info cache wedges in a long-lived
    # process after a USB reset / kernel re-enumeration. Each Reconnect
    # click increments the counter by 1 (the reconnect path calls
    # detect() exactly once). Background scheduler capture failures also
    # increment. The cooldown below prevents loop-restarts when the
    # camera is genuinely absent.
    MAX_CONSECUTIVE_INIT_FAILURES = 3
    SERVICE_RESTART_COOLDOWN_SEC = 300

    def __init__(self) -> None:
        import gphoto2 as gp  # noqa: PLC0415 - imported lazily on real Pi only

        self._gp = gp
        self._cam: Any | None = None
        self._info: CameraInfo | None = None
        self._configured: bool = False
        # J: count of consecutive ensure() failures since the last
        # successful init. Reset to 0 on any successful Camera().init().
        self._consecutive_init_failures: int = 0
        # libgphoto2 is not thread-safe at the device level. Without this
        # lock, concurrent /api/camera/* requests (the Camera page fires
        # info, settings and properties in parallel via React Query) all
        # race into gp.Camera().init() at once — only one wins, the rest
        # collide on the USB interface and return -7 "I/O problem". An
        # RLock serialises every public method below; reentrancy means
        # methods that delegate (e.g. capture() -> _ensure()) don't
        # deadlock against themselves. The capture watchdog Timer
        # deliberately stays outside the lock — it touches self._cam
        # directly so it can force-close a hung handle from another
        # thread without waiting for the capture call to release.
        self._lock: threading.RLock = threading.RLock()

    # ---- handle lifecycle (B, C) ----------------------------------------

    # Fail-fast window: if we have a fresh failure on record, the next
    # _ensure() call does ONE attempt with no retry sleeps. Without this
    # check, calling detect() while the camera is unplugged would tie
    # up an event-loop worker for ~14 s (1 + 3 + 10 s of backoff),
    # which makes /api/health, /api/home and /api/camera/info crawl.
    FAIL_FAST_WINDOW_SEC = 30.0

    def _ensure(self) -> Any:
        """Return a live Camera handle, creating one if needed.

        Retries init up to 3 times (1 s / 3 s / 10 s) on transient errors
        so a brief EBUSY (the kernel re-enumerating after a USB reset,
        or another process holding the bus for a moment) doesn't
        permanently fail us — BUT only when there's been no recent
        failure on record. After a recent failure we fail-fast on a
        single attempt to keep the API event loop responsive while the
        camera is physically absent or locked up.

        Serialised by self._lock so concurrent callers don't race into
        gp.Camera().init() and collide on the USB interface (-7 errors).
        """
        import time as _t  # noqa: PLC0415

        with self._lock:
            # G: respect post-reset grace.
            if self._reset_grace_remaining() > 0:
                raise self._gp.GPhoto2Error(-1, "post-reset grace period")

            if self._cam is not None:
                return self._cam

            # Decide retry budget based on recent health.
            try:
                from arclap_station.camera.health import read_state  # noqa: PLC0415
                from datetime import datetime as _dt, UTC as _UTC  # noqa: PLC0415

                st = read_state()
                err_at = st.get("last_error_at")
                if err_at:
                    try:
                        age = (_dt.now(_UTC) - _dt.fromisoformat(err_at)).total_seconds()
                        fail_fast = 0 <= age <= self.FAIL_FAST_WINDOW_SEC
                    except (ValueError, TypeError):
                        fail_fast = False
                else:
                    fail_fast = False
            except Exception:  # noqa: BLE001
                fail_fast = False

            backoff: list[float] = [0.0] if fail_fast else [1.0, 3.0, 10.0]
            last_exc: Exception | None = None
            for attempt, wait in enumerate(backoff, start=1):
                try:
                    cam = self._gp.Camera()
                    cam.init()
                    self._cam = cam
                    self._configured = False
                    self._after_init(cam)
                    # J: any successful init clears the stuck-camera
                    # counter so transient hiccups don't accumulate
                    # toward a restart over the course of a long
                    # uptime.
                    self._consecutive_init_failures = 0
                    return cam
                except self._gp.GPhoto2Error as exc:
                    last_exc = exc
                    if not fail_fast:
                        log.info(
                            "camera init attempt %d/%d failed: %s — retrying in %.1fs",
                            attempt,
                            len(backoff),
                            exc,
                            wait,
                        )
                    if attempt < len(backoff) and wait > 0:
                        _t.sleep(wait)
            # All attempts exhausted. Bump the consecutive-failure
            # counter. Recovery escalation ladder at the threshold:
            #   1. USB bus power-cycle (uhubctl) — fixes a wedged camera
            #      USB controller without losing the service. No-op if
            #      no power-switchable hub / uhubctl present.
            #   2. Service self-restart — clears wedged libgphoto2 state
            #      in our own address space.
            # The check stays inside the lock so two concurrent failures
            # can't both trip the threshold.
            assert last_exc is not None
            self._consecutive_init_failures += 1
            if (
                self._consecutive_init_failures
                >= self.MAX_CONSECUTIVE_INIT_FAILURES
            ):
                if self._try_usb_power_cycle():
                    # Power-cycle ran — reset the counter and let the
                    # next ensure() re-probe the freshly-reset bus
                    # instead of immediately self-restarting.
                    self._consecutive_init_failures = 0
                else:
                    self._trigger_service_restart(last_exc)
            raise last_exc

    def _after_init(self, cam: Any) -> None:
        """Apply our per-session camera settings — fail-soft.

        These calls are best-effort: not every body exposes every path,
        and a failure here must NOT prevent capture. We log + continue.
        """
        if self._configured:
            return
        # B: disable camera auto-power-off so PTP idle gaps don't put
        # the body into deep sleep. Path differs by body / firmware —
        # try the common ones.
        for path in (
            "/main/settings/autopoweroff",
            "/main/settings/sleeptimer",
            "/main/settings/datetimeutc",  # ensures we have config write access
        ):
            try:
                widget = cam.get_single_config(path)
                if "autopoweroff" in path or "sleeptimer" in path:
                    widget.set_value("0")
                    cam.set_single_config(path, widget)
                    log.info("camera auto-power-off disabled (%s)", path)
                    break
            except self._gp.GPhoto2Error:
                continue
            except Exception as exc:  # noqa: BLE001
                log.debug("set %s failed: %s", path, exc)
        # E: write captures to camera RAM, not the SD/CF card. We
        # pull the file out of RAM right after capture, so this
        # eliminates card-full / card-protected / slow-card failures.
        for path, value in (
            ("/main/settings/capturetarget", "0"),
            ("/main/settings/capturetarget", "Internal RAM"),
        ):
            try:
                widget = cam.get_single_config(path)
                widget.set_value(value)
                cam.set_single_config(path, widget)
                log.info("capture target set to internal RAM (%s=%s)", path, value)
                break
            except self._gp.GPhoto2Error:
                continue
            except Exception as exc:  # noqa: BLE001
                log.debug("capture-target set failed: %s", exc)
        self._configured = True

    def _drop_handle(self) -> None:
        if self._cam is not None:
            try:
                self._cam.exit()
            except Exception:  # noqa: BLE001 - libgphoto2 is fussy
                pass
        self._cam = None
        self._configured = False

    # ---- keepalive (K) ---------------------------------------------------

    def keepalive(self) -> bool:
        """Keep an existing PTP session warm so the camera doesn't sleep
        between scheduled captures.

        Only acts when a live handle already exists — it does NOT force
        an init (that's _ensure's job and would fight a genuinely-absent
        camera). A cheap battery-level read is enough to reset the
        body's idle timer. Serialised by the lock so it never collides
        with a capture. Returns True if the keepalive poll succeeded.
        """
        with self._lock:
            if self._cam is None:
                return False
            try:
                _safe_config(self._cam, self._gp, "/main/status/batterylevel")
                return True
            except Exception:  # noqa: BLE001
                # A failed keepalive means the session went stale; drop
                # the handle so the next _ensure() rebuilds it cleanly.
                self._drop_handle()
                return False

    def _try_usb_power_cycle(self) -> bool:
        """Attempt a hardware USB power-cycle to recover a wedged camera.

        Drops our handle first (so we're not holding a dead fd across
        the power cut), then asks the usbhub helper to cycle bus power.
        Returns True only if a power-cycle actually ran (i.e. uhubctl +
        a switchable hub are present). False → caller falls back to the
        service self-restart.
        """
        try:
            from arclap_station.hardware.usbhub import power_cycle_usb  # noqa: PLC0415

            self._drop_handle()
            return power_cycle_usb()
        except Exception as exc:  # noqa: BLE001
            log.debug("usb power-cycle attempt failed: %s", exc)
            return False

    # ---- stuck-camera self-restart (J) -----------------------------------

    def _trigger_service_restart(self, last_exc: Exception) -> None:
        """Last-resort recovery: terminate so systemd brings us back fresh.

        libgphoto2's port-info cache lives in process memory. Certain
        kernel-level USB events (re-enumeration after sleep, ``echo 0
        >authorized`` resets, hub power-cycles) can leave the cache
        pointing at a port descriptor that no longer exists. Every
        subsequent ``Camera().init()`` then returns ``-7 I/O problem``
        even though ``gphoto2 --auto-detect`` from a fresh CLI process
        succeeds. The only known cure is a fresh address space.

        We rely on systemd's ``Restart=always RestartSec=3`` to bring us
        back within ~3 s. A sentinel file enforces a cooldown so that a
        genuinely-absent camera doesn't loop-restart us indefinitely;
        once the cooldown expires, if the camera is still stuck, we
        restart once more.
        """
        import os as _os  # noqa: PLC0415
        import time as _t  # noqa: PLC0415

        sentinel = Path("/var/lib/arclap/last_camera_restart")
        try:
            last = float(sentinel.read_text().strip())
            if _t.time() - last < self.SERVICE_RESTART_COOLDOWN_SEC:
                log.warning(
                    "stuck-camera self-restart suppressed — last fired %.0fs ago "
                    "(cooldown %.0fs); %d consecutive init failures",
                    _t.time() - last,
                    self.SERVICE_RESTART_COOLDOWN_SEC,
                    self._consecutive_init_failures,
                )
                # Reset counter so we don't try again immediately on the
                # next ensure(). The cooldown is the rate-limit.
                self._consecutive_init_failures = 0
                return
        except (OSError, ValueError):
            # No sentinel yet, or it's malformed — first restart in
            # this boot is allowed.
            pass

        try:
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_text(str(_t.time()))
        except OSError as exc:
            log.warning("could not write restart sentinel: %s", exc)

        # Audit log entry per CLAUDE.md §12.10 — the cockpit needs to
        # show "service auto-restarted to clear stuck camera" in the
        # Recent Activity feed so operators understand the bump.
        try:
            from arclap_station.audit import emit as audit_emit  # noqa: PLC0415

            audit_emit(
                "system",
                "camera.service_restart",
                {
                    "reason": "stuck_libgphoto2",
                    "consecutive_failures": self._consecutive_init_failures,
                    "last_error": str(last_exc),
                },
            )
        except Exception:  # noqa: BLE001
            # Audit must not block restart — if the audit log itself
            # is stuck we still want recovery.
            pass

        log.error(
            "stuck-camera self-restart firing after %d consecutive init failures "
            "(last error: %s) — systemd will restart us in 3s",
            self._consecutive_init_failures,
            last_exc,
        )
        # os._exit bypasses Python's atexit / SIGTERM handlers; a clean
        # shutdown might re-enter libgphoto2 to release the wedged
        # handle and hang there, which is exactly what we're trying to
        # escape. Exit code 1 → systemd treats it as failure → restart.
        _os._exit(1)

    # ---- post-reset grace (G) -------------------------------------------

    def _reset_grace_remaining(self) -> float:
        """Seconds left in the post-USB-reset grace window, or 0.0."""
        try:
            from arclap_station.camera.health import read_last_reset_age  # noqa: PLC0415

            age = read_last_reset_age()
        except Exception:  # noqa: BLE001
            return 0.0
        if age is None or age >= self.POST_RESET_GRACE_SEC:
            return 0.0
        return self.POST_RESET_GRACE_SEC - age

    # ---- probes ----------------------------------------------------------

    def detect(self) -> CameraInfo:
        with self._lock:
            try:
                cam = self._ensure()
            except self._gp.GPhoto2Error as exc:
                log.info("no camera detected: %s", exc)
                self._drop_handle()
                self._beacon_failure(str(exc))
                return CameraInfo(detected=False)

            try:
                summary = str(cam.get_summary())
            except self._gp.GPhoto2Error:
                summary = ""
            info = CameraInfo(
                detected=True,
                model=_safe_config(cam, self._gp, "/main/status/cameramodel"),
                serial=_safe_config(cam, self._gp, "/main/status/serialnumber"),
                battery=_safe_config(cam, self._gp, "/main/status/batterylevel"),
                lens=_safe_config(cam, self._gp, "/main/status/lensname"),
                firmware=_safe_config(cam, self._gp, "/main/status/cameramodel"),
                shutter_count=_safe_int_config(cam, self._gp, "/main/status/shuttercounter"),
                summary=summary[:2048] if summary else None,
            )
            self._info = info
            self._beacon_ok(info.model)
            return info

    def get_config(self, path: str) -> Any:
        with self._lock:
            cam = self._ensure()
            return _safe_config(cam, self._gp, path)

    def set_config(self, path: str, value: Any) -> None:
        with self._lock:
            cam = self._ensure()
            widget = cam.get_single_config(path)
            widget.set_value(value)
            cam.set_single_config(path, widget)

    def list_config(self) -> dict[str, Any]:
        from arclap_station.camera.properties import widget_tree_to_dict  # noqa: PLC0415

        with self._lock:
            cam = self._ensure()
            return widget_tree_to_dict(cam.get_config())

    # ---- capture (D, E, I) ----------------------------------------------

    def _wake_probe(self, cam: Any) -> None:
        """Cheap config GET to wake the camera before capture (D).

        On Canon, reading battery level forces a PTP round-trip without
        opening a new operation. If the camera is in standby, this is
        what brings it back to life. If it errors, we surface a fast
        failure rather than waiting for the slower capture to time out.
        """
        try:
            _safe_config(cam, self._gp, "/main/status/batterylevel")
        except Exception:  # noqa: BLE001
            pass

    def capture(self, dest_dir: Path) -> Path:
        import threading as _th  # noqa: PLC0415

        gp = self._gp
        # I: arm a watchdog timer that force-closes the handle if the
        # capture call doesn't return within CAPTURE_TIMEOUT_SEC.
        # The timer fires on a separate thread and intentionally does NOT
        # acquire self._lock — it pokes self._cam.exit() directly so it
        # can rescue a hung capture without deadlocking against the
        # locked capture() body below.
        timed_out = {"flag": False}

        def _kill() -> None:
            timed_out["flag"] = True
            log.warning(
                "capture exceeded %.0fs — force-closing handle",
                self.CAPTURE_TIMEOUT_SEC,
            )
            try:
                if self._cam is not None:
                    self._cam.exit()
            except Exception:  # noqa: BLE001
                pass

        timer = _th.Timer(self.CAPTURE_TIMEOUT_SEC, _kill)
        timer.daemon = True
        timer.start()
        try:
            with self._lock:
                try:
                    cam = self._ensure()
                    self._wake_probe(cam)
                    file_path = cam.capture(gp.GP_CAPTURE_IMAGE)
                except gp.GPhoto2Error as exc:
                    log.info("camera capture failed (%s); reinitialising and retrying", exc)
                    self._drop_handle()
                    cam = self._ensure()
                    self._wake_probe(cam)
                    file_path = cam.capture(gp.GP_CAPTURE_IMAGE)

                if timed_out["flag"]:
                    raise gp.GPhoto2Error(-1, "capture timed out, handle force-closed")

                cam_file = cam.file_get(
                    file_path.folder, file_path.name, gp.GP_FILE_TYPE_NORMAL
                )
                target = dest_dir / file_path.name
                if target.exists():
                    import time as _t  # noqa: PLC0415

                    stem, dot, ext = file_path.name.rpartition(".")
                    ns = _t.time_ns()
                    target = dest_dir / (
                        f"{stem or file_path.name}-{ns}{('.' + ext) if dot else ''}"
                    )
                cam_file.save(str(target))
                try:
                    cam.file_delete(file_path.folder, file_path.name)
                except gp.GPhoto2Error:
                    pass
                self._beacon_ok(self._info.model if self._info else None)
                return Path(str(target))
        except Exception as exc:
            self._beacon_failure(str(exc))
            raise
        finally:
            timer.cancel()

    def capture_preview(self) -> bytes:
        with self._lock:
            try:
                cam = self._ensure()
                cam_file = cam.capture_preview()
                data = cam_file.get_data_and_size()
                self._beacon_ok(self._info.model if self._info else None)
                return bytes(data)
            except Exception as exc:
                self._beacon_failure(str(exc))
                raise

    def close(self) -> None:
        with self._lock:
            self._drop_handle()

    # ---- health beacon (F) ----------------------------------------------

    def _beacon_ok(self, model: str | None) -> None:
        try:
            from arclap_station.camera.health import write_ok  # noqa: PLC0415

            write_ok(model)
        except Exception:  # noqa: BLE001
            pass

    def _beacon_failure(self, err: str) -> None:
        try:
            from arclap_station.camera.health import write_failure  # noqa: PLC0415

            write_failure(err)
        except Exception:  # noqa: BLE001
            pass


def _safe_config(cam: Any, gp: Any, path: str) -> Any:
    try:
        w = cam.get_single_config(path)
        return w.get_value()
    except gp.GPhoto2Error:
        return None
    except Exception:  # noqa: BLE001
        return None


def _safe_int_config(cam: Any, gp: Any, path: str) -> int | None:
    v = _safe_config(cam, gp, path)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ----- Singleton accessor ------------------------------------------------

_adapter: CameraAdapter | None = None
_adapter_lock = threading.Lock()


def _build_backend() -> CameraBackend:
    settings = get_settings()
    if settings.use_mock_camera:
        from arclap_station.camera.mock import MockCamera  # noqa: PLC0415

        return MockCamera()
    try:
        return _GPhoto2Backend()
    except ImportError:
        log.warning("python-gphoto2 not installed — falling back to MockCamera")
        from arclap_station.camera.mock import MockCamera  # noqa: PLC0415

        return MockCamera()


def get_adapter() -> CameraAdapter:
    global _adapter
    with _adapter_lock:
        if _adapter is None:
            _adapter = CameraAdapter(_build_backend())
    return _adapter


def set_adapter(adapter: CameraAdapter | None) -> None:
    """Test hook to inject a specific adapter."""
    global _adapter
    with _adapter_lock:
        _adapter = adapter
