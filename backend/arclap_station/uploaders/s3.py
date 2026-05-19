"""S3 uploader using boto3 (and S3-compatible endpoints like MinIO / R2)."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from arclap_station.uploaders import UploadError, register

log = logging.getLogger(__name__)


class S3Uploader:
    type = "s3"

    def __init__(self, uploader_id: str, name: str, config: dict[str, Any]) -> None:
        import boto3  # noqa: PLC0415

        self.id = uploader_id
        self.name = name
        self.bucket = config["bucket"]
        self.prefix = config.get("prefix", "").lstrip("/")
        self.region = config.get("region", "eu-central-1")
        self.endpoint_url = config.get("endpoint_url")
        self.acl = config.get("acl")

        session_kwargs: dict[str, Any] = {}
        if config.get("access_key_id"):
            session_kwargs["aws_access_key_id"] = config["access_key_id"]
        if config.get("secret_access_key"):
            session_kwargs["aws_secret_access_key"] = config["secret_access_key"]
        if self.region:
            session_kwargs["region_name"] = self.region

        client_kwargs: dict[str, Any] = {"region_name": self.region}
        if self.endpoint_url:
            client_kwargs["endpoint_url"] = self.endpoint_url

        self._session = boto3.session.Session(**session_kwargs)
        self._client = self._session.client("s3", **client_kwargs)

    def _key(self, suffix: str) -> str:
        if self.prefix:
            return f"{self.prefix.rstrip('/')}/{suffix.lstrip('/')}"
        return suffix.lstrip("/")

    def test(self) -> dict[str, Any]:
        key = self._key(f"arclap-probe-{int(time.time())}.txt")
        try:
            self._client.put_object(Bucket=self.bucket, Key=key, Body=b"x")
            obj = self._client.get_object(Bucket=self.bucket, Key=key)
            body = obj["Body"].read()
            if body != b"x":
                raise UploadError("s3 probe body mismatch")
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:  # noqa: BLE001 - botocore raises a tower of exception classes
            raise UploadError(f"s3 probe failed: {exc}") from exc
        return {"ok": True, "bucket": self.bucket, "region": self.region}

    def upload(self, local: Path, key: str) -> dict[str, Any]:
        target = self._key(key)
        extra: dict[str, Any] = {}
        if self.acl:
            extra["ACL"] = self.acl
        try:
            self._client.upload_file(str(local), self.bucket, target, ExtraArgs=extra or None)
        except Exception as exc:  # noqa: BLE001
            raise UploadError(f"s3 upload failed: {exc}") from exc
        return {"ok": True, "bucket": self.bucket, "key": target}

    def delete_remote(self, key: str) -> bool:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass


@register("s3")
def _build(uploader_id: str, name: str, config: dict[str, Any]) -> S3Uploader:
    return S3Uploader(uploader_id, name, config)
