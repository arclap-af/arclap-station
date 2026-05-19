"""In-memory mock camera backend — used for tests and on machines without libgphoto2.

The mock generates a tiny valid JPEG so downstream code (Pillow thumbnailer,
file upload) can run end-to-end with no real device.
"""

from __future__ import annotations

import io
import struct
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arclap_station.camera.adapter import CameraInfo


def _tiny_jpeg(seed: int = 0) -> bytes:
    """A genuinely tiny valid 8x8 JPEG with a seed-driven luminance."""
    # Pillow is heavy at import time; we use struct/bytes instead so this stays cheap.
    try:
        from PIL import Image  # noqa: PLC0415

        img = Image.new("RGB", (16, 16), color=(seed % 255, (seed * 7) % 255, (seed * 13) % 255))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 - last-resort fallback for super-minimal envs
        # Bare-minimum SOI + EOI marker — not a viewable JPEG but bytes
        return struct.pack(">HH", 0xFFD8, 0xFFD9)


class MockCamera:
    """A deterministic in-memory camera for tests."""

    def __init__(
        self,
        model: str = "Canon EOS R6 (mock)",
        battery: str = "92%",
        lens: str = "RF 24-70 F2.8 L IS USM (mock)",
    ) -> None:
        self._info = CameraInfo(
            detected=True,
            model=model,
            port="usb:001,004",
            serial="MOCK-SN-0001",
            battery=battery,
            lens=lens,
            firmware="1.8.1",
            shutter_count=12_418,
            summary="Mock camera summary\n",
        )
        self._lock = threading.RLock()
        self._counter = 0
        self._config: dict[str, Any] = {
            "/main/imgsettings/iso": {
                "path": "/main/imgsettings/iso",
                "label": "ISO",
                "type": "radio",
                "value": "400",
                "choices": ["100", "200", "400", "800", "1600", "3200"],
                "readonly": False,
            },
            "/main/imgsettings/imageformat": {
                "path": "/main/imgsettings/imageformat",
                "label": "Image Format",
                "type": "radio",
                "value": "Large Fine JPEG",
                "choices": ["Large Fine JPEG", "RAW", "RAW + JPEG"],
                "readonly": False,
            },
            "/main/capturesettings/shutterspeed": {
                "path": "/main/capturesettings/shutterspeed",
                "label": "Shutter Speed",
                "type": "radio",
                "value": "1/125",
                "choices": ["1/2000", "1/1000", "1/500", "1/250", "1/125", "1/60", "1/30"],
                "readonly": False,
            },
            "/main/capturesettings/aperture": {
                "path": "/main/capturesettings/aperture",
                "label": "Aperture",
                "type": "radio",
                "value": "8",
                "choices": ["2.8", "4", "5.6", "8", "11", "16"],
                "readonly": False,
            },
            "/main/status/batterylevel": {
                "path": "/main/status/batterylevel",
                "label": "Battery Level",
                "type": "text",
                "value": battery,
                "readonly": True,
            },
        }

    def detect(self) -> CameraInfo:
        return self._info

    def get_config(self, path: str) -> Any:
        with self._lock:
            entry = self._config.get(path)
            return entry["value"] if entry else None

    def set_config(self, path: str, value: Any) -> None:
        with self._lock:
            entry = self._config.get(path)
            if entry is None:
                raise KeyError(f"unknown config path: {path}")
            if entry.get("readonly"):
                raise PermissionError(f"config is read-only: {path}")
            if "choices" in entry and value not in entry["choices"]:
                raise ValueError(f"{value!r} not in choices {entry['choices']!r}")
            entry["value"] = value

    def list_config(self) -> dict[str, Any]:
        with self._lock:
            # Add section markers so the tree walker semantics still apply.
            tree: dict[str, Any] = {
                "/main": {"path": "/main", "label": "Main", "type": "window", "readonly": False},
                "/main/imgsettings": {
                    "path": "/main/imgsettings",
                    "label": "Image Settings",
                    "type": "section",
                    "readonly": False,
                },
                "/main/capturesettings": {
                    "path": "/main/capturesettings",
                    "label": "Capture Settings",
                    "type": "section",
                    "readonly": False,
                },
                "/main/status": {
                    "path": "/main/status",
                    "label": "Camera Status",
                    "type": "section",
                    "readonly": True,
                },
            }
            tree.update({k: dict(v) for k, v in self._config.items()})
            return tree

    def capture(self, dest_dir: Path) -> Path:
        with self._lock:
            self._counter += 1
            now = datetime.now(UTC)
            name = f"ph_{int(now.timestamp())}_{self._counter:04d}.jpg"
            target = dest_dir / name
            dest_dir.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_tiny_jpeg(self._counter))
            return target

    def capture_preview(self) -> bytes:
        with self._lock:
            self._counter += 1
            return _tiny_jpeg(self._counter)

    def close(self) -> None:
        # Nothing to release.
        return None
