"""MQTT uploader: stub paho.mqtt.Client."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from arclap_station.uploaders.mqtt import MQTTUploader


class _FakeInfo:
    def __init__(self) -> None:
        self.rc = 0

    def wait_for_publish(self, timeout: float | None = None) -> None:
        return None


class _FakeClient:
    instances: list["_FakeClient"] = []

    def __init__(self, *a: Any, **kw: Any) -> None:
        self.published: list[tuple[str, bytes | str]] = []
        self._on_publish: Any = None
        _FakeClient.instances.append(self)

    def username_pw_set(self, user: str, password: str = "") -> None:
        return None

    def tls_set(self, **kw: Any) -> None:
        return None

    def connect(self, host: str, port: int, keepalive: int = 60) -> int:
        return 0

    def loop_start(self) -> None:
        return None

    def loop_stop(self) -> None:
        return None

    def publish(self, topic: str, payload: bytes | str, qos: int = 0) -> _FakeInfo:
        self.published.append((topic, payload))
        if self._on_publish:
            self._on_publish(self, None, 1, type("R", (), {"value": 0})(), None)
        return _FakeInfo()

    @property
    def on_publish(self) -> Any:
        return self._on_publish

    @on_publish.setter
    def on_publish(self, fn: Any) -> None:
        self._on_publish = fn

    def disconnect(self) -> None:
        return None


@pytest.fixture()
def fake_mqtt(monkeypatch: pytest.MonkeyPatch) -> type[_FakeClient]:
    _FakeClient.instances.clear()

    class _NS:
        VERSION2 = 2

    monkeypatch.setattr("arclap_station.uploaders.mqtt.mqtt.Client", _FakeClient)
    monkeypatch.setattr("arclap_station.uploaders.mqtt.mqtt.CallbackAPIVersion", _NS)
    monkeypatch.setattr("arclap_station.uploaders.mqtt.mqtt.MQTT_ERR_SUCCESS", 0)

    # The uploader now does a bounded TCP reachability probe before
    # connecting (so a black-holed broker fails fast instead of wedging
    # a worker). Stub it for these publish-logic tests, which never
    # touch a real broker. (test_mqtt_connect_timeout.py covers the
    # probe's failure path explicitly.)
    class _FakeSock:
        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "arclap_station.uploaders.mqtt.socket.create_connection",
        lambda *a, **k: _FakeSock(),
    )
    return _FakeClient


def test_mqtt_probe(fake_mqtt: type[_FakeClient]) -> None:
    u = MQTTUploader("u", "m", {"host": "h", "port": 8883})
    res = u.test()
    assert res["ok"]
    assert fake_mqtt.instances


def test_mqtt_upload(fake_mqtt: type[_FakeClient], tmp_path: Path) -> None:
    src = tmp_path / "p.jpg"
    src.write_bytes(b"abc")
    u = MQTTUploader(
        "u", "m", {"host": "h", "port": 8883, "topic_root": "captures"}
    )
    res = u.upload(src, "2026/05/19/ph.jpg")
    assert res["ok"]
    instance = fake_mqtt.instances[-1]
    assert instance.published
    topic, _ = instance.published[-1]
    assert topic == "captures/2026/05/19/ph.jpg"
