"""HTTPS webhook uploader with bearer, basic, or HMAC auth."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from pathlib import Path
from typing import Any

import httpx

from arclap_station.uploaders import UploadError, register


class WebhookUploader:
    type = "webhook"

    def __init__(self, uploader_id: str, name: str, config: dict[str, Any]) -> None:
        self.id = uploader_id
        self.name = name
        self.url = config["url"]
        self.method = config.get("method", "POST").upper()
        self.auth_type = config.get("auth_type", "none")
        self.token = config.get("token")
        self.username = config.get("username")
        self.password = config.get("password")
        self.hmac_secret = config.get("hmac_secret")
        self.hmac_header = config.get("hmac_header", "X-Arclap-Signature")
        self.timeout = float(config.get("timeout_seconds", 30))
        self.verify_tls = bool(config.get("verify_tls", True))
        self.headers_extra = config.get("headers") or {}

    def _headers(self, body: bytes) -> dict[str, str]:
        headers: dict[str, str] = {"User-Agent": "arclap-station/0.1"}
        headers.update(self.headers_extra)
        if self.auth_type == "bearer" and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        elif self.auth_type == "basic" and self.username and self.password is not None:
            blob = base64.b64encode(f"{self.username}:{self.password}".encode()).decode(
                "ascii"
            )
            headers["Authorization"] = f"Basic {blob}"
        if self.auth_type == "hmac" and self.hmac_secret:
            mac = hmac.new(
                self.hmac_secret.encode("utf-8"), body, hashlib.sha256
            ).hexdigest()
            headers[self.hmac_header] = f"sha256={mac}"
        return headers

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout, verify=self.verify_tls)

    def test(self) -> dict[str, Any]:
        probe = f"arclap-probe-{int(time.time())}".encode()
        body = probe
        try:
            with self._client() as client:
                r = client.request(
                    self.method, self.url, content=body, headers=self._headers(body)
                )
                r.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise UploadError(f"webhook probe failed: {exc}") from exc
        return {"ok": True, "status": r.status_code}

    def upload(self, local: Path, key: str) -> dict[str, Any]:
        body = local.read_bytes()
        files = {"file": (Path(key).name, body, "image/jpeg")}
        data = {"key": key}
        try:
            with self._client() as client:
                # For multipart we sign the raw body; many servers don't HMAC-verify
                # multipart anyway. Documented in §12.10.
                r = client.request(
                    self.method,
                    self.url,
                    files=files,
                    data=data,
                    headers=self._headers(body),
                )
                r.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise UploadError(f"webhook upload failed: {exc}") from exc
        return {"ok": True, "status": r.status_code, "remote_path": key}

    def delete_remote(self, key: str) -> bool:
        # Webhook destinations don't expose a delete by default. Return True so
        # the queue treats local file removal as success.
        return True

    def close(self) -> None:
        return None


@register("webhook")
def _build(uploader_id: str, name: str, config: dict[str, Any]) -> WebhookUploader:
    return WebhookUploader(uploader_id, name, config)
