"""Webhook uploader: synchronous MockTransport echo server."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from arclap_station.uploaders.webhook import WebhookUploader


class _Recorder:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []


@pytest.fixture()
def fake_http(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    """Replace httpx.Client in the webhook module with a transport-backed client."""
    rec = _Recorder()

    def handler(request: httpx.Request) -> httpx.Response:
        rec.posts.append(
            {
                "url": str(request.url),
                "method": request.method,
                "headers": dict(request.headers),
                "len": len(request.content),
            }
        )
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)

    class _PatchedClient(httpx.Client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.pop("verify", None)
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("arclap_station.uploaders.webhook.httpx.Client", _PatchedClient)
    return rec


def test_webhook_probe(fake_http: _Recorder) -> None:
    u = WebhookUploader(
        "u1",
        "hook",
        {
            "url": "http://hook.local/hook",
            "method": "POST",
            "auth_type": "bearer",
            "token": "t",
        },
    )
    result = u.test()
    assert result["ok"]
    assert fake_http.posts


def test_webhook_upload_sends_file(fake_http: _Recorder, tmp_path: Path) -> None:
    f = tmp_path / "p.jpg"
    f.write_bytes(b"hello world")
    u = WebhookUploader(
        "u1",
        "hook",
        {"url": "http://hook.local/hook", "method": "POST", "auth_type": "none"},
    )
    res = u.upload(f, "2026/05/19/ph_0001.jpg")
    assert res["ok"]
    assert fake_http.posts
    last = fake_http.posts[-1]
    assert last["len"] >= len(b"hello world")


def test_webhook_hmac_header(fake_http: _Recorder) -> None:
    u = WebhookUploader(
        "u1",
        "hook",
        {
            "url": "http://hook.local/hook",
            "method": "POST",
            "auth_type": "hmac",
            "hmac_secret": "shh",
            "hmac_header": "X-Sig",
        },
    )
    u.test()
    last = fake_http.posts[-1]
    sig = next((v for k, v in last["headers"].items() if k.lower() == "x-sig"), None)
    assert sig is not None
    assert sig.startswith("sha256=")


def test_webhook_basic_auth_header(fake_http: _Recorder) -> None:
    u = WebhookUploader(
        "u1",
        "hook",
        {
            "url": "http://hook.local/hook",
            "method": "POST",
            "auth_type": "basic",
            "username": "alice",
            "password": "wonderland",
        },
    )
    u.test()
    last = fake_http.posts[-1]
    authz = next((v for k, v in last["headers"].items() if k.lower() == "authorization"), "")
    assert authz.startswith("Basic ")
