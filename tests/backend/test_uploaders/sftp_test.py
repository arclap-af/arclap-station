"""SFTP uploader: stubbed paramiko."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from arclap_station.uploaders import UploadError
from arclap_station.uploaders.sftp import SFTPUploader


class _FakeSFTP:
    def __init__(self, files: dict[str, bytes]):
        self.files = files

    def stat(self, p: str) -> Any:
        if p in self.files or any(k.startswith(p + "/") for k in self.files):
            return type("S", (), {"st_size": 1})
        raise FileNotFoundError(p)

    def mkdir(self, p: str) -> None:
        self.files[p + "/"] = b""

    def file(self, p: str, mode: str) -> Any:
        if "w" in mode:
            buf = io.BytesIO()

            class _Sink:
                def __enter__(_self) -> "_Sink":
                    return _self

                def __exit__(_self, *a: Any) -> None:
                    self.files[p] = buf.getvalue()

                def write(_self, data: bytes) -> int:
                    return buf.write(data)

            return _Sink()
        # read
        data = self.files.get(p, b"")
        buf = io.BytesIO(data)

        class _Src:
            def __enter__(_self) -> "_Src":
                return _self

            def __exit__(_self, *a: Any) -> None:
                pass

            def read(_self) -> bytes:
                return buf.read()

        return _Src()

    def put(self, local: str, remote: str) -> None:
        self.files[remote] = Path(local).read_bytes()

    def remove(self, p: str) -> None:
        self.files.pop(p, None)

    def close(self) -> None:
        pass


class _FakeSSH:
    def __init__(self, files: dict[str, bytes]):
        self._sftp = _FakeSFTP(files)

    def open_sftp(self) -> _FakeSFTP:
        return self._sftp

    def get_transport(self) -> Any:
        return None

    def close(self) -> None:
        pass


@pytest.fixture()
def fake_paramiko(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    files: dict[str, bytes] = {}

    def fake_connect(self: Any, **kw: Any) -> None:
        self._files = files

    monkeypatch.setattr(
        "arclap_station.uploaders.sftp.paramiko.SSHClient.connect", fake_connect
    )

    def fake_open_sftp(self: Any) -> _FakeSFTP:
        return _FakeSFTP(files)

    monkeypatch.setattr(
        "arclap_station.uploaders.sftp.paramiko.SSHClient.open_sftp", fake_open_sftp
    )
    monkeypatch.setattr(
        "arclap_station.uploaders.sftp.paramiko.SSHClient.set_missing_host_key_policy",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "arclap_station.uploaders.sftp.paramiko.SSHClient.load_host_keys",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "arclap_station.uploaders.sftp.paramiko.SSHClient.get_transport", lambda self: None
    )
    monkeypatch.setattr("arclap_station.uploaders.sftp.paramiko.SSHClient.close", lambda self: None)
    return files


def test_sftp_probe(fake_paramiko: dict[str, bytes]) -> None:
    u = SFTPUploader(
        "u",
        "sftp",
        {
            "host": "h",
            "username": "u",
            "password": "p",
            "path": "/incoming",
            "strict_host_key": False,
        },
    )
    res = u.test()
    assert res["ok"]


def test_sftp_upload(fake_paramiko: dict[str, bytes], tmp_path: Path) -> None:
    src = tmp_path / "p.jpg"
    src.write_bytes(b"abc")
    u = SFTPUploader(
        "u",
        "sftp",
        {
            "host": "h",
            "username": "u",
            "password": "p",
            "path": "/incoming",
            "strict_host_key": False,
        },
    )
    res = u.upload(src, "2026/05/19/ph.jpg")
    assert res["ok"]
    assert "/incoming/2026/05/19/ph.jpg" in fake_paramiko


def test_sftp_connect_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: Any, **kw: Any) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(
        "arclap_station.uploaders.sftp.paramiko.SSHClient.connect", boom
    )
    monkeypatch.setattr(
        "arclap_station.uploaders.sftp.paramiko.SSHClient.set_missing_host_key_policy",
        lambda *a, **kw: None,
    )
    u = SFTPUploader(
        "u",
        "sftp",
        {"host": "h", "username": "u", "password": "p", "strict_host_key": False},
    )
    with pytest.raises(UploadError):
        u.test()
