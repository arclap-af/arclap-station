"""MQTT uploader — publishes a metadata envelope (and optionally bytes) for Arclap Cloud."""

from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

from arclap_station.uploaders import UploadError, register


class MQTTUploader:
    type = "mqtt"

    def __init__(self, uploader_id: str, name: str, config: dict[str, Any]) -> None:
        self.id = uploader_id
        self.name = name
        self.host = config["host"]
        self.port = int(config.get("port", 8883))
        self.client_id = config.get("client_id", f"arclap-{uploader_id}")
        self.username = config.get("username")
        self.password = config.get("password")
        self.use_tls = bool(config.get("tls", True))
        self.ca_pem = config.get("ca_pem")
        self.cert_pem = config.get("cert_pem")
        self.key_pem = config.get("key_pem")
        self.topic_root = config.get("topic_root", "arclap/photos")
        self.qos = int(config.get("qos", 1))
        self.timeout = float(config.get("timeout_seconds", 10))
        self.publish_payload = bool(config.get("publish_payload", False))

    def _client(self) -> mqtt.Client:
        api_version = getattr(mqtt, "CallbackAPIVersion", None)
        if api_version is not None and hasattr(api_version, "VERSION2"):
            c = mqtt.Client(
                api_version.VERSION2,
                client_id=self.client_id,
                clean_session=True,
            )
        else:
            # paho-mqtt < 2.0 fallback
            c = mqtt.Client(client_id=self.client_id, clean_session=True)
        if self.username:
            c.username_pw_set(self.username, self.password or "")
        if self.use_tls:
            c.tls_set(
                ca_certs=self.ca_pem,
                certfile=self.cert_pem,
                keyfile=self.key_pem,
            )
        return c

    def _publish_blocking(self, topic: str, payload: bytes | str) -> int:
        client = self._client()
        done = threading.Event()
        result: dict[str, int] = {"rc": -1}

        def on_publish(_c: Any, _u: Any, _mid: int, reason_code: Any, _properties: Any) -> None:
            try:
                result["rc"] = int(reason_code.value if hasattr(reason_code, "value") else reason_code)
            except Exception:  # noqa: BLE001
                result["rc"] = 0
            done.set()

        client.on_publish = on_publish
        try:
            client.connect(self.host, self.port, keepalive=int(self.timeout) + 5)
            client.loop_start()
            info = client.publish(topic, payload, qos=self.qos)
            info.wait_for_publish(timeout=self.timeout)
            done.wait(timeout=self.timeout)
        finally:
            client.loop_stop()
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        return result["rc"]

    def test(self) -> dict[str, Any]:
        topic = f"{self.topic_root}/_probe"
        envelope = {"type": "probe", "ts": int(time.time())}
        try:
            rc = self._publish_blocking(topic, json.dumps(envelope))
        except Exception as exc:  # noqa: BLE001
            raise UploadError(f"mqtt probe failed: {exc}") from exc
        if rc not in (0, mqtt.MQTT_ERR_SUCCESS):
            raise UploadError(f"mqtt probe returned rc={rc}")
        return {"ok": True, "topic": topic}

    def upload(self, local: Path, key: str) -> dict[str, Any]:
        envelope: dict[str, Any] = {
            "type": "photo",
            "key": key,
            "size": local.stat().st_size,
            "ts": int(time.time()),
        }
        if self.publish_payload:
            envelope["payload_b64"] = base64.b64encode(local.read_bytes()).decode("ascii")
        topic = f"{self.topic_root}/{key}".replace("//", "/")
        try:
            rc = self._publish_blocking(topic, json.dumps(envelope))
        except Exception as exc:  # noqa: BLE001
            raise UploadError(f"mqtt publish failed: {exc}") from exc
        if rc not in (0, mqtt.MQTT_ERR_SUCCESS):
            raise UploadError(f"mqtt publish returned rc={rc}")
        return {"ok": True, "topic": topic}

    def delete_remote(self, key: str) -> bool:
        # MQTT publishes are append-only.
        return True

    def close(self) -> None:
        return None


@register("mqtt")
def _build(uploader_id: str, name: str, config: dict[str, Any]) -> MQTTUploader:
    return MQTTUploader(uploader_id, name, config)
