"""FTP / FTPS-explicit uploader using stdlib ftplib."""

from __future__ import annotations

import ftplib
import io
import ssl
import time
from pathlib import Path
from typing import Any

from arclap_station.uploaders import UploadError, register


class FTPUploader:
    type = "ftp"

    def __init__(self, uploader_id: str, name: str, config: dict[str, Any]) -> None:
        self.id = uploader_id
        self.name = name
        self.host = config["host"]
        self.port = int(config.get("port", 21))
        self.username = config.get("username", "anonymous")
        self.password = config.get("password", "")
        self.root = (config.get("path") or "/").rstrip("/") or "/"
        self.passive = bool(config.get("passive", True))
        self.use_tls = bool(config.get("tls", False))
        self.timeout = float(config.get("timeout_seconds", 15))

    def _connect(self) -> ftplib.FTP:
        if self.use_tls:
            ftp: ftplib.FTP = ftplib.FTP_TLS(timeout=self.timeout, context=ssl.create_default_context())
        else:
            ftp = ftplib.FTP(timeout=self.timeout)
        ftp.connect(self.host, self.port, timeout=self.timeout)
        ftp.login(user=self.username, passwd=self.password)
        if self.use_tls and isinstance(ftp, ftplib.FTP_TLS):
            ftp.prot_p()
        ftp.set_pasv(self.passive)
        if self.root and self.root != "/":
            self._ensure_cwd(ftp, self.root)
        return ftp

    def _ensure_cwd(self, ftp: ftplib.FTP, path: str) -> None:
        parts = [p for p in path.split("/") if p]
        cwd = "/" if path.startswith("/") else ""
        if cwd:
            ftp.cwd("/")
        for p in parts:
            try:
                ftp.cwd(p)
            except ftplib.error_perm:
                ftp.mkd(p)
                ftp.cwd(p)

    def test(self) -> dict[str, Any]:
        try:
            ftp = self._connect()
        except Exception as exc:  # noqa: BLE001
            raise UploadError(f"ftp connect failed: {exc}") from exc
        try:
            key = f"arclap-probe-{int(time.time())}.txt"
            ftp.storbinary(f"STOR {key}", io.BytesIO(b"x"))
            sink = io.BytesIO()
            ftp.retrbinary(f"RETR {key}", sink.write)
            if sink.getvalue() != b"x":
                raise UploadError("ftp probe body mismatch")
            ftp.delete(key)
        except Exception as exc:  # noqa: BLE001
            raise UploadError(f"ftp probe failed: {exc}") from exc
        finally:
            try:
                ftp.quit()
            except Exception:  # noqa: BLE001
                ftp.close()
        return {"ok": True, "host": self.host, "tls": self.use_tls}

    def upload(self, local: Path, key: str) -> dict[str, Any]:
        ftp = self._connect()
        try:
            target_parent = str(Path(key).parent).replace("\\", "/")
            if target_parent and target_parent != ".":
                self._ensure_cwd(ftp, target_parent)
            with local.open("rb") as fh:
                ftp.storbinary(f"STOR {Path(key).name}", fh)
        except Exception as exc:  # noqa: BLE001
            raise UploadError(f"ftp upload failed: {exc}") from exc
        finally:
            try:
                ftp.quit()
            except Exception:  # noqa: BLE001
                ftp.close()
        return {"ok": True, "remote_path": key}

    def delete_remote(self, key: str) -> bool:
        try:
            ftp = self._connect()
        except Exception:  # noqa: BLE001
            return False
        try:
            ftp.delete(key)
            return True
        except Exception:  # noqa: BLE001
            return False
        finally:
            try:
                ftp.quit()
            except Exception:  # noqa: BLE001
                ftp.close()

    def close(self) -> None:
        return None


@register("ftp")
def _build(uploader_id: str, name: str, config: dict[str, Any]) -> FTPUploader:
    return FTPUploader(uploader_id, name, config)


@register("ftps")
def _build_ftps(uploader_id: str, name: str, config: dict[str, Any]) -> FTPUploader:
    return FTPUploader(uploader_id, name, {**config, "tls": True})
