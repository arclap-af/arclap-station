"""S3 uploader: stubbed boto3 client."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from arclap_station.uploaders.s3 import S3Uploader


class _FakeS3:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: Any, **_: Any) -> dict[str, Any]:
        data = Body.read() if hasattr(Body, "read") else bytes(Body)
        self.store[(Bucket, Key)] = data
        return {}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        return {"Body": io.BytesIO(self.store[(Bucket, Key)])}

    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.store.pop((Bucket, Key), None)
        return {}

    def upload_file(self, local: str, bucket: str, key: str, ExtraArgs: Any = None) -> None:
        self.store[(bucket, key)] = Path(local).read_bytes()

    def close(self) -> None:
        pass


@pytest.fixture()
def fake_boto(monkeypatch: pytest.MonkeyPatch) -> _FakeS3:
    import boto3  # noqa: PLC0415

    fake = _FakeS3()

    class _Session:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        def client(self, name: str, **kw: Any) -> _FakeS3:  # noqa: ARG002
            return fake

    monkeypatch.setattr(boto3.session, "Session", _Session, raising=True)
    return fake


def test_s3_probe(fake_boto: _FakeS3) -> None:
    u = S3Uploader(
        "u",
        "s3",
        {"bucket": "b", "region": "eu-central-1", "prefix": "captures/"},
    )
    res = u.test()
    assert res["ok"]


def test_s3_upload(fake_boto: _FakeS3, tmp_path: Path) -> None:
    src = tmp_path / "p.jpg"
    src.write_bytes(b"abc")
    u = S3Uploader("u", "s3", {"bucket": "b", "region": "eu-central-1", "prefix": "p/"})
    res = u.upload(src, "2026/05/19/ph.jpg")
    assert res["ok"]
    assert ("b", "p/2026/05/19/ph.jpg") in fake_boto.store
