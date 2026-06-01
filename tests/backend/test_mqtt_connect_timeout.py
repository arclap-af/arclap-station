"""MQTT uploader connect is bounded.

paho's connect() blocks on the OS default socket timeout (minutes)
against a black-holed broker, which would wedge an upload-worker
thread. The uploader now does a bounded TCP reachability probe first;
this test proves an unreachable broker fails fast as an UploadError
rather than hanging.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from arclap_station.uploaders import UploadError
from arclap_station.uploaders.mqtt import MQTTUploader


def test_unreachable_broker_fails_fast() -> None:
    up = MQTTUploader("d1", "Test", {"broker": "mqtt://192.0.2.1:1883", "timeout": 1})

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("connection refused")

    # Patch the bounded probe to fail instantly — no real network wait.
    with patch("arclap_station.uploaders.mqtt.socket.create_connection", side_effect=boom):
        with pytest.raises(UploadError) as ei:
            up.test()
    assert "unreachable" in str(ei.value).lower()
