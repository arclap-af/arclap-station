"""HTTPS webhook uploader with bearer, basic, or HMAC auth."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from pathlib import Path
from typing import Any

import httpx

from arclap_station.uploaders import UploadError, pick, pick_bool, register


class WebhookUploader:
    type = "webhook"

    def __init__(self, uploader_id: str, name: str, config: dict[str, Any]) -> None:
        self.id = uploader_id
        self.name = name
        url = pick(config, "url", "endpoint")
        if not url:
            raise ValueError("webhook uploader requires 'url'")
        self.url = url
        self.method = str(pick(config, "method", default="POST")).upper()
        # `auth_header` is the UI's single field — if present, treat as a
        # bearer-style "Authorization: <value>" without parsing.
        auth_header = pick(config, "auth_header")
        if auth_header:
            self.auth_type = "raw"
            self.raw_authorization: str | None = str(auth_header)
        else:
            self.auth_type = pick(config, "auth_type", "auth", default="none")
            self.raw_authorization = None
        self.token = pick(config, "token", "bearer", "api_key")
        self.username = pick(config, "username", "user")
        self.password = pick(config, "password", "passwd")
        self.hmac_secret = pick(config, "hmac_secret", "signing_secret", "secret")
        self.hmac_header = pick(config, "hmac_header", default="X-Arclap-Signature")
        self.timeout = float(pick(config, "timeout_seconds", "timeout", default=30))
        self.verify_tls = pick_bool(config, "verify_tls", "verify", default=True)
        self.headers_extra = pick(config, "headers", "extra_headers", default={}) or {}

    def _headers(self, body: bytes) -> dict[str, str]:
        headers: dict[str, str] = {"User-Agent": "arclap-station/0.2"}
        headers.update(self.headers_extra)
        # Honour the UI's "auth_header" string verbatim if set.
        if self.raw_authorization:
            headers["Authorization"] = self.raw_authorization
        elif self.auth_type == "bearer" and self.token:
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
        # Mirror the upload() multipart shape so a real photo-intake
        # webhook (which typically only accepts multipart/form-data
        # with a `file` part) doesn't false-fail the probe. Earlier
        # versions POSTed raw bytes which returned 400/415 against
        # the very intakes the operator is trying to configure.
        body = b"\xff\xd8\xff\xd9"  # 4-byte syntactically-valid JPEG
        probe_name = f"arclap-probe-{int(time.time())}.jpg"
        files = {"file": (probe_name, body, "image/jpeg")}
        data = {"key": probe_name}
        try:
            with self._client() as client:
                r = client.request(
                    self.method,
                    self.url,
                    files=files,
                    data=data,
                    headers=self._headers(body),
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


@register("arc")
def _build_arc(uploader_id: str, name: str, config: dict[str, Any]) -> WebhookUploader:
    """`arc` is the cockpit's friendly name for Arclap Cloud. Until the
    cloud-mediated tunnel ships, we treat it as a webhook destination —
    the cloud just needs a URL + bearer token to receive uploads."""
    return WebhookUploader(uploader_id, name, config)
