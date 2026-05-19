"""Local uploader: filesystem round-trip."""

from __future__ import annotations

from pathlib import Path

import pytest

from arclap_station.uploaders import UploadError
from arclap_station.uploaders.local import LocalUploader


def test_local_probe(tmp_path: Path) -> None:
    u = LocalUploader("u1", "nas", {"path": str(tmp_path / "out")})
    result = u.test()
    assert result["ok"] is True


def test_local_upload_and_delete(tmp_path: Path) -> None:
    root = tmp_path / "out"
    src = tmp_path / "src.jpg"
    src.write_bytes(b"abc")
    u = LocalUploader("u1", "nas", {"path": str(root)})
    res = u.upload(src, "2026/05/19/ph_0001.jpg")
    assert res["ok"]
    target = root / "2026/05/19/ph_0001.jpg"
    assert target.exists()
    assert target.read_bytes() == b"abc"
    assert u.delete_remote("2026/05/19/ph_0001.jpg")
    assert not target.exists()


def test_local_requires_path() -> None:
    with pytest.raises(ValueError):
        LocalUploader("u1", "nas", {})


def test_local_retention_sweep(tmp_path: Path) -> None:
    root = tmp_path / "out"
    u = LocalUploader("u1", "nas", {"path": str(root), "retention_days": 1})
    # Just make sure sweep doesn't blow up when there's nothing aged.
    u._sweep()
