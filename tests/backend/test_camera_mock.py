"""Mock-camera adapter behaviour."""

from __future__ import annotations

from pathlib import Path

import pytest

from arclap_station.camera.adapter import get_adapter
from arclap_station.camera.mock import MockCamera


def test_detect_returns_info() -> None:
    adapter = get_adapter()
    info = adapter.detect()
    assert info.detected is True
    assert info.model is not None
    assert info.battery is not None


def test_capture_writes_file(tmp_path: Path) -> None:
    adapter = get_adapter()
    target = adapter.capture(tmp_path)
    assert target.exists()
    assert target.stat().st_size > 0


def test_capture_preview_returns_bytes() -> None:
    adapter = get_adapter()
    data = adapter.capture_preview()
    assert isinstance(data, bytes)
    assert len(data) > 100  # tiny but not empty


def test_set_config_roundtrip() -> None:
    adapter = get_adapter()
    adapter.set_config("/main/imgsettings/iso", "800")
    assert adapter.get_config("/main/imgsettings/iso") == "800"


def test_set_config_rejects_invalid_choice() -> None:
    adapter = get_adapter()
    with pytest.raises(ValueError):
        adapter.set_config("/main/imgsettings/iso", "9999")


def test_set_config_rejects_unknown_path() -> None:
    adapter = get_adapter()
    with pytest.raises(KeyError):
        adapter.set_config("/main/does/not/exist", "x")


def test_set_config_readonly_blocks() -> None:
    cam = MockCamera()
    with pytest.raises(PermissionError):
        cam.set_config("/main/status/batterylevel", "0%")


def test_list_config_has_sections_and_leaves() -> None:
    tree = get_adapter().list_config()
    assert "/main" in tree
    assert tree["/main/imgsettings/iso"]["choices"]
