"""SFTP uploader using paramiko, with host-key checking."""

from __future__ import annotations

import io
import logging
import time
from pathlib import Path
from typing import Any

import paramiko
from paramiko.ssh_exception import SSHException

from arclap_station.uploaders import UploadError, register

log = logging.getLogger(__name__)


class SFTPUploader:
    type = "sftp"

    def __init__(self, uploader_id: str, name: str, config: dict[str, Any]) -> None:
        self.id = uploader_id
        self.name = name
        self.host = config["host"]
        self.port = int(config.get("port", 22))
        self.username = config["username"]
        self.password = config.get("password")
        self.private_key_pem = config.get("private_key_pem")
        self.private_key_passphrase = config.get("private_key_passphrase")
        self.known_hosts_pem = config.get("known_hosts_pem")
        self.host_key_fp = config.get("host_key_fingerprint")
        self.root = (config.get("path") or ".").rstrip("/")
        self.timeout = float(config.get("timeout_seconds", 15))
        self.strict_host = bool(config.get("strict_host_key", True))

    def _connect(self) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        if self.known_hosts_pem:
            try:
                client.load_host_keys(io.StringIO(self.known_hosts_pem))  # type: ignore[arg-type]
            except (SSHException, OSError) as exc:
                log.warning("invalid known_hosts: %s", exc)
        client.set_missing_host_key_policy(
            paramiko.RejectPolicy() if self.strict_host else paramiko.AutoAddPolicy()
        )
        connect_kwargs: dict[str, Any] = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
            "timeout": self.timeout,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if self.password:
            connect_kwargs["password"] = self.password
        if self.private_key_pem:
            key = paramiko.RSAKey.from_private_key(
                io.StringIO(self.private_key_pem), password=self.private_key_passphrase
            )
            connect_kwargs["pkey"] = key

        try:
            client.connect(**connect_kwargs)
        except Exception as exc:  # noqa: BLE001
            raise UploadError(f"sftp connect failed: {exc}") from exc

        if self.host_key_fp:
            transport = client.get_transport()
            if transport is not None:
                remote_key = transport.get_remote_server_key()
                fp_actual = remote_key.get_fingerprint().hex()
                if fp_actual.lower() != self.host_key_fp.lower().replace(":", ""):
                    client.close()
                    raise UploadError(f"sftp host key mismatch: got {fp_actual}")

        return client

    def _open(self) -> tuple[paramiko.SSHClient, paramiko.SFTPClient]:
        ssh = self._connect()
        sftp = ssh.open_sftp()
        return ssh, sftp

    def _ensure_dir(self, sftp: paramiko.SFTPClient, path: str) -> None:
        parts = [p for p in path.split("/") if p]
        cwd = "/" if path.startswith("/") else ""
        for p in parts:
            cwd = (cwd.rstrip("/") + "/" + p) if cwd else p
            try:
                sftp.stat(cwd)
            except FileNotFoundError:
                sftp.mkdir(cwd)

    def test(self) -> dict[str, Any]:
        ssh, sftp = self._open()
        try:
            self._ensure_dir(sftp, self.root)
            key = f"{self.root}/arclap-probe-{int(time.time())}.txt"
            with sftp.file(key, "wb") as fh:
                fh.write(b"x")
            with sftp.file(key, "rb") as fh:
                data = fh.read()
            if data != b"x":
                raise UploadError("sftp probe body mismatch")
            sftp.remove(key)
        except Exception as exc:  # noqa: BLE001
            raise UploadError(f"sftp probe failed: {exc}") from exc
        finally:
            sftp.close()
            ssh.close()
        return {"ok": True, "host": self.host, "root": self.root}

    def upload(self, local: Path, key: str) -> dict[str, Any]:
        ssh, sftp = self._open()
        remote = f"{self.root}/{key}".replace("//", "/")
        try:
            self._ensure_dir(sftp, str(Path(remote).parent))
            sftp.put(str(local), remote)
        except Exception as exc:  # noqa: BLE001
            raise UploadError(f"sftp upload failed: {exc}") from exc
        finally:
            sftp.close()
            ssh.close()
        return {"ok": True, "remote_path": remote}

    def delete_remote(self, key: str) -> bool:
        ssh, sftp = self._open()
        remote = f"{self.root}/{key}".replace("//", "/")
        try:
            sftp.remove(remote)
            return True
        except Exception:  # noqa: BLE001
            return False
        finally:
            sftp.close()
            ssh.close()

    def close(self) -> None:
        return None


@register("sftp")
def _build(uploader_id: str, name: str, config: dict[str, Any]) -> SFTPUploader:
    return SFTPUploader(uploader_id, name, config)
