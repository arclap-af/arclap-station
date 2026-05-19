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

    # Hard timeout for one shutter release + buffered readout. libgphoto2 is
    # known to deadlock when PTP encounters bus weirdness — without this the
    # scheduler silently freezes for the rest of the deployment.
    CAPTURE_TIMEOUT_SEC = 45.0
    PREVIEW_TIMEOUT_SEC = 5.0

    def capture(self, dest_dir: Path | None = None) -> Path:
        target = dest_dir or self._default_capture_dir()
        target.mkdir(parents=True, exist_ok=True)
        return self._run_with_timeout(
            lambda: self._capture_inner(target),
            self.CAPTURE_TIMEOUT_SEC,
            "capture",
        )

    def _capture_inner(self, target: Path) -> Path:
        with self._lock:
            return self._backend.capture(target)

    def capture_preview(self) -> bytes:
        return self._run_with_timeout(
            self._preview_inner, self.PREVIEW_TIMEOUT_SEC, "capture_preview"
        )

    def _preview_inner(self) -> bytes:
        with self._lock:
            return self._backend.capture_preview()

    def _run_with_timeout(self, fn: Any, timeout: float, op: str) -> Any:
        """Run `fn` in a worker thread; if it doesn't return within `timeout`,
        forcibly close the backend handle and raise TimeoutError.

        Python threads can't be killed cleanly, so on timeout we (a) call
        `backend.close()` to release the libgphoto2 handle (some bodies
        respond to this even from another thread), and (b) let the rogue
        thread die in the background — the next request gets a fresh
        Camera() because `close()` zeroes self._cam.
        """
        import concurrent.futures  # noqa: PLC0415

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(fn)
            try:
                return fut.result(timeout=timeout)
            except concurrent.futures.TimeoutError as exc:
                log.error("camera %s timed out after %.1fs — forcing reinit", op, timeout)
                try:
                    self._backend.close()
                except Exception:  # noqa: BLE001
                    pass
                raise TimeoutError(f"camera {op} exceeded {timeout}s") from exc

    def close(self) -> None:
        with self._lock:
            self._backend.close()

    @staticmethod
    def _default_capture_dir() -> Path:
        now = datetime.now(UTC)
        root = get_settings().paths.photos
        return root / f"{now.year:04d}" / f"{now.month:02d}" / f"{now.day:02d}"


class _GPhoto2Backend:
    """Real backend using python-gphoto2."""

    def __init__(self) -> None:
        import gphoto2 as gp  # noqa: PLC0415 - imported lazily on real Pi only

        self._gp = gp
        self._cam: Any | None = None
        self._info: CameraInfo | None = None

    def _ensure(self) -> Any:
        if self._cam is None:
            cam = self._gp.Camera()
            cam.init()
            self._cam = cam
        return self._cam

    def detect(self) -> CameraInfo:
        try:
            cam = self._ensure()
        except self._gp.GPhoto2Error as exc:
            log.info("no camera detected: %s", exc)
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
        return info

    def get_config(self, path: str) -> Any:
        cam = self._ensure()
        return _safe_config(cam, self._gp, path)

    def set_config(self, path: str, value: Any) -> None:
        cam = self._ensure()
        widget = cam.get_single_config(path)
        widget.set_value(value)
        cam.set_single_config(path, widget)

    def list_config(self) -> dict[str, Any]:
        from arclap_station.camera.properties import widget_tree_to_dict  # noqa: PLC0415

        cam = self._ensure()
        return widget_tree_to_dict(cam.get_config())

    def capture(self, dest_dir: Path) -> Path:
        gp = self._gp
        cam = self._ensure()
        file_path = cam.capture(gp.GP_CAPTURE_IMAGE)
        cam_file = cam.file_get(file_path.folder, file_path.name, gp.GP_FILE_TYPE_NORMAL)
        target = dest_dir / file_path.name
        cam_file.save(str(target))
        try:
            cam.file_delete(file_path.folder, file_path.name)
        except gp.GPhoto2Error:
            pass
        return Path(str(target))

    def capture_preview(self) -> bytes:
        cam = self._ensure()
        cam_file = cam.capture_preview()
        data = cam_file.get_data_and_size()
        return bytes(data)

    def close(self) -> None:
        if self._cam is not None:
            try:
                self._cam.exit()
            except Exception:  # noqa: BLE001 - libgphoto2 is fussy
                pass
            self._cam = None


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
