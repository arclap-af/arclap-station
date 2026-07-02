"""FTP / FTPS-explicit uploader using stdlib ftplib."""

from __future__ import annotations

import ftplib
import io
import socket
import ssl
import time
from pathlib import Path
from typing import Any

from arclap_station.uploaders import UploadError, expand_placeholders, pick, pick_bool, register


class _ImplicitFTP_TLS(ftplib.FTP_TLS):  # noqa: N801 - mirrors stdlib ftplib.FTP_TLS
    """FTPS *implicit* — TLS from the first byte (default port 990),
    unlike explicit FTPS which starts plaintext and upgrades via AUTH TLS.
    stdlib ftplib only speaks explicit, so we wrap the control socket in
    TLS immediately on connect."""

    def connect(self, host: str = "", port: int = 0, timeout: float = -999,
                source_address: Any = None) -> str:
        if host:
            self.host = host
        if port > 0:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        self.sock = socket.create_connection((self.host, self.port), self.timeout,
                                              source_address=source_address)
        self.af = self.sock.family
        self.sock = self.context.wrap_socket(self.sock, server_hostname=self.host)
        self.file = self.sock.makefile("r")
        self.welcome = self.getresp()
        return self.welcome


class FTPUploader:
    type = "ftp"

    def __init__(self, uploader_id: str, name: str, config: dict[str, Any]) -> None:
        self.id = uploader_id
        self.name = name
        host = pick(config, "host", "hostname")
        if not host:
            raise ValueError("ftp uploader requires 'host'")
        self.host = host
        self.port = int(pick(config, "port", default=21))
        self.username = pick(config, "username", "user", "login", default="anonymous")
        self.password = pick(config, "password", "passwd", "pass", default="")
        self.root = expand_placeholders(
            str(pick(config, "path", "remote_path", "root", default="/"))
        ).rstrip("/") or "/"
        # `passive` is the canonical key; `mode == "passive"` or "active" is
        # the UI form. `security` field from the UI form maps to `tls`.
        self.passive = pick_bool(
            config, "passive",
            default=str(pick(config, "mode", default="passive")).lower() == "passive",
        )
        # The Security select sends "ftps_explicit" (the DEFAULT),
        # "ftps_implicit", or "plain". The old check only matched the
        # literal "ftps"/"tls", so EVERY FTPS selection silently fell
        # through to plaintext FTP with credentials in the clear. Any
        # ftps* value must encrypt; "plain" is the only cleartext mode.
        # The "Encrypt in transit" toggle also forces TLS.
        sec = str(pick(config, "security", default="")).lower()
        tls_from_sec = sec.startswith("ftps") or sec == "tls"
        self.use_tls = pick_bool(
            config, "tls", "ftps", "encrypt_in_transit", default=tls_from_sec
        )
        self.implicit_tls = sec in ("ftps_implicit", "implicit")
        self.timeout = float(pick(config, "timeout_seconds", "timeout", default=15))

    def _connect(self) -> ftplib.FTP:
        if self.use_tls:
            ctx = ssl.create_default_context()
            cls = _ImplicitFTP_TLS if self.implicit_tls else ftplib.FTP_TLS
            ftp: ftplib.FTP = cls(timeout=self.timeout, context=ctx)
        else:
            ftp = ftplib.FTP(timeout=self.timeout)
        port = self.port
        if self.implicit_tls and port == 21:
            port = 990  # implicit FTPS default port
        ftp.connect(self.host, port, timeout=self.timeout)
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
            # Use a .jpg extension — many camera-photo FTP intakes
            # (e.g. teleport.io, ftptoday/ipcam services, Tuya FTP)
            # reject anything that isn't .jpg/.mp4 with a generic
            # `534 Request denied for policy reasons`, which is
            # indistinguishable from a TLS policy mismatch unless you
            # know to look for the welcome banner. The 4 bytes below
            # are a syntactically-minimal JPEG (SOI + EOI markers) so
            # content-sniffing intakes accept them.
            key = f"arclap-probe-{int(time.time())}.jpg"
            ftp.storbinary(f"STOR {key}", io.BytesIO(b"\xff\xd8\xff\xd9"))
            # We deliberately don't RETR or require DELE to succeed.
            # Upload-only intakes typically move the file out of the
            # client-visible tree on STOR completion; RETR comes back
            # 550 and DELE comes back 550 even though the upload was
            # accepted. The STOR succeeding IS the proof the
            # destination is usable. Best-effort cleanup below.
            try:
                ftp.delete(key)
            except Exception:  # noqa: BLE001 - DELE may be policy-blocked
                pass
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
