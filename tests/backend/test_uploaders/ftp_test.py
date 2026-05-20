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


def test_ftp_probe_uses_jpg_extension(fake_ftp: type[_FakeFTP]) -> None:
    """Regression: camera-photo FTP intakes (teleport.io, ipcam services)
    reject any non-.jpg/.mp4 upload with `534 Request denied for policy
    reasons`. Our probe used to STOR `.txt` and broke against these hosts.
    """
    u = FTPUploader("u", "ftp", {"host": "h", "username": "u", "password": "p"})
    u.test()
    # Find the STOR'd file in the fake fs — it must have a .jpg suffix.
    assert _FakeFTP.instances, "no FTP connection made"
    stored = list(_FakeFTP.instances[-1].files.keys())
    # delete() may have removed it; capture during STOR instead.
    # The fake delete drops the file, so we re-run with a delete-blocking
    # fake to inspect what was stored.
    pass  # presence-test handled below via test_ftp_probe_upload_only


def test_ftp_probe_upload_only_intake(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: upload-only intakes (Teleport, ipcam services) accept
    STOR but reject RETR and DELE — and reject any non-photo extension
    with 534. Our probe must succeed against such a host.
    """
    import ftplib as _ftplib

    captured: dict[str, str | bytes] = {}

    class _UploadOnlyFTP:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        def connect(self, host: str, port: int, timeout: float | None = None) -> None:  # noqa: ARG002
            pass

        def login(self, user: str = "", passwd: str = "") -> None:  # noqa: ARG002
            pass

        def set_pasv(self, on: bool) -> None:  # noqa: ARG002
            pass

        def storbinary(self, cmd: str, src: Any) -> None:
            name = cmd.split(" ", 1)[1]
            # Teleport-style policy: only .jpg / .mp4 allowed.
            if not (name.endswith(".jpg") or name.endswith(".mp4")):
                raise _ftplib.error_perm(
                    "534 Request denied for policy reasons."
                )
            captured["name"] = name
            captured["body"] = src.read()

        def retrbinary(self, cmd: str, cb: Any) -> None:  # noqa: ARG002
            raise _ftplib.error_perm("534 Request denied for policy reasons.")

        def delete(self, name: str) -> None:  # noqa: ARG002
            raise _ftplib.error_perm("550 File does not exist.")

        def cwd(self, p: str) -> None:  # noqa: ARG002
            pass

        def quit(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr("arclap_station.uploaders.ftp.ftplib.FTP", _UploadOnlyFTP)
    u = FTPUploader("u", "ftp", {"host": "h", "username": "u", "password": "p"})
    res = u.test()
    assert res["ok"] is True
    assert captured["name"].endswith(".jpg"), (
        f"probe filename {captured['name']!r} must end with .jpg "
        f"so camera-FTP intakes don't reject it with 534"
    )
    # Body must be a valid (minimal) JPEG — SOI + EOI markers.
    assert captured["body"].startswith(b"\xff\xd8") and captured["body"].endswith(
        b"\xff\xd9"
    ), "probe body must be a syntactically-valid JPEG"


def test_ftp_upload(fake_ftp: type[_FakeFTP], tmp_path: Path) -> None:
    src = tmp_path / "p.jpg"
    src.write_bytes(b"abc")
    u = FTPUploader("u", "ftp", {"host": "h", "username": "u", "password": "p"})
    res = u.upload(src, "2026/05/19/ph.jpg")
    assert res["ok"]
