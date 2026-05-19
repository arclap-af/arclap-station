"""FTP uploader: stubbed ftplib."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from arclap_station.uploaders import UploadError
from arclap_station.uploaders.ftp import FTPUploader


class _FakeFTP:
    instances: list["_FakeFTP"] = []

    def __init__(self, *a: Any, **kw: Any) -> None:
        self.files: dict[str, bytes] = {}
        self._pwd: str = "/"
        self.passive = True
        _FakeFTP.instances.append(self)

    def connect(self, host: str, port: int, timeout: float | None = None) -> None:  # noqa: ARG002
        return None

    def login(self, user: str = "", passwd: str = "") -> None:  # noqa: ARG002
        return None

    def set_pasv(self, on: bool) -> None:
        self.passive = on

    def storbinary(self, cmd: str, src: Any) -> None:
        name = cmd.split(" ", 1)[1]
        full = self._resolve(name)
        self.files[full] = src.read()

    def retrbinary(self, cmd: str, callback: Any) -> None:
        name = cmd.split(" ", 1)[1]
        full = self._resolve(name)
        callback(self.files.get(full, b""))

    def delete(self, name: str) -> None:
        self.files.pop(self._resolve(name), None)

    def cwd(self, p: str) -> None:
        if p == "/":
            self._pwd = "/"
        elif p.startswith("/"):
            self._pwd = p.rstrip("/") or "/"
        else:
            self._pwd = (self._pwd.rstrip("/") + "/" + p)

    def mkd(self, p: str) -> None:
        self.files[self._resolve(p) + "/"] = b""

    def quit(self) -> None:
        pass

    def close(self) -> None:
        pass

    def _resolve(self, name: str) -> str:
        if name.startswith("/"):
            return name
        return (self._pwd.rstrip("/") + "/" + name).lstrip("/") or "/"


@pytest.fixture()
def fake_ftp(monkeypatch: pytest.MonkeyPatch) -> type[_FakeFTP]:
    _FakeFTP.instances.clear()
    monkeypatch.setattr("arclap_station.uploaders.ftp.ftplib.FTP", _FakeFTP)
    return _FakeFTP


def test_ftp_probe(fake_ftp: type[_FakeFTP]) -> None:
    u = FTPUploader("u", "ftp", {"host": "h", "username": "u", "password": "p"})
    res = u.test()
    assert res["ok"]


def test_ftp_upload(fake_ftp: type[_FakeFTP], tmp_path: Path) -> None:
    src = tmp_path / "p.jpg"
    src.write_bytes(b"abc")
    u = FTPUploader("u", "ftp", {"host": "h", "username": "u", "password": "p"})
    res = u.upload(src, "2026/05/19/ph.jpg")
    assert res["ok"]
