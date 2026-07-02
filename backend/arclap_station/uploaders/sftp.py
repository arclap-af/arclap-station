"""SFTP uploader using paramiko, with host-key checking."""

from __future__ import annotations

import io
import logging
import time
from pathlib import Path
from typing import Any

import paramiko
from paramiko.ssh_exception import SSHException

from arclap_station.uploaders import UploadError, expand_placeholders, pick, pick_bool, register

log = logging.getLogger(__name__)


class SFTPUploader:
    type = "sftp"

    def __init__(self, uploader_id: str, name: str, config: dict[str, Any]) -> None:
        self.id = uploader_id
        self.name = name
        host = pick(config, "host", "hostname")
        if not host:
            raise ValueError("sftp uploader requires 'host'")
        self.host = host
        self.port = int(pick(config, "port", default=22))
        username = pick(config, "username", "user", "login")
        if not username:
            raise ValueError("sftp uploader requires 'username'")
        self.username = username
        self.password = pick(config, "password", "passwd", "pass")
        self.private_key_pem = pick(config, "private_key_pem", "private_key", "key")
        self.private_key_passphrase = pick(config, "private_key_passphrase", "key_passphrase")
        self.known_hosts_pem = pick(config, "known_hosts_pem", "known_hosts")
        self.host_key_fp = pick(config, "host_key_fingerprint", "fingerprint")
        # Default to '.' (cwd of the SFTP login) so users who don't set a
        # remote path get a sensible target.
        self.root = expand_placeholders(
            str(pick(config, "path", "remote_path", "root", default="."))
        ).rstrip("/")
        if not self.root:
            self.root = "."
        self.timeout = float(pick(config, "timeout_seconds", "timeout", default=15))
        # Default strict_host_key to False — the cockpit can't ask the
        # operator to paste a host key fingerprint mid-test. Document
        # in the form that production deployments should set it True.
        self.strict_host = pick_bool(config, "strict_host_key", "strict_host", default=False)

    def _load_private_key(self) -> Any:
        """Load the private key, trying modern types first. ssh-keygen
        defaults to Ed25519 now; the old RSAKey-only path failed on every
        Ed25519/ECDSA key an operator pasted in."""
        errors: list[str] = []
        for key_cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey, paramiko.DSSKey):
            try:
                return key_cls.from_private_key(
                    io.StringIO(self.private_key_pem),
                    password=self.private_key_passphrase or None,
                )
            except (paramiko.SSHException, ValueError) as exc:
                errors.append(f"{key_cls.__name__}: {exc}")
        raise UploadError("sftp: unsupported or invalid private key (" + "; ".join(errors[:2]) + ")")

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
            connect_kwargs["pkey"] = self._load_private_key()

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
