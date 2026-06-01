"""MQTT uploader — publishes a metadata envelope (and optionally bytes) for Arclap Cloud."""

from __future__ import annotations

import base64
import json
import socket
import threading
import time
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

from arclap_station.uploaders import UploadError, pick, pick_bool, register


def _parse_broker_url(url: str) -> tuple[str, int, bool]:
    """Parse 'ssl://host:port' / 'mqtts://...' / 'mqtt://host' style URLs."""
    if "://" in url:
        scheme, rest = url.split("://", 1)
    else:
        scheme, rest = "mqtt", url
    use_tls = scheme.lower() in ("mqtts", "ssl", "tls")
    host_port = rest.rstrip("/").split("/")[0]
    if ":" in host_port:
        host, port_s = host_port.rsplit(":", 1)
        try:
            port = int(port_s)
        except ValueError:
            port = 8883 if use_tls else 1883
    else:
        host = host_port
        port = 8883 if use_tls else 1883
    return host, port, use_tls


class MQTTUploader:
    type = "mqtt"

    def __init__(self, uploader_id: str, name: str, config: dict[str, Any]) -> None:
        self.id = uploader_id
        self.name = name
        # Accept either {host, port, tls} or a single {broker} URL.
        broker_url = pick(config, "broker", "broker_url", "url")
        if broker_url:
            host, port, url_tls = _parse_broker_url(str(broker_url))
        else:
            host, port, url_tls = pick(config, "host", "hostname"), int(pick(config, "port", default=8883)), True
        if not host:
            raise ValueError("mqtt uploader requires 'host' or 'broker' URL")
        self.host = host
        self.port = int(pick(config, "port", default=port))
        self.client_id = pick(config, "client_id", "clientId", default=f"arclap-{uploader_id}")
        self.username = pick(config, "username", "user")
        self.password = pick(config, "password", "passwd")
        self.use_tls = pick_bool(config, "tls", "use_tls", "ssl", default=url_tls)
        self.ca_pem = pick(config, "ca_pem", "ca_cert", "ca")
        self.cert_pem = pick(config, "cert_pem", "client_cert", "cert")
        self.key_pem = pick(config, "key_pem", "client_key", "key")
        # UI uses `topic` for the root path.
        self.topic_root = pick(config, "topic_root", "topic", "topic_prefix", default="arclap/photos")
        self.qos = int(pick(config, "qos", default=1))
        self.timeout = float(pick(config, "timeout_seconds", "timeout", default=10))
        self.publish_payload = pick_bool(config, "publish_payload", default=False)

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
            # paho's connect() blocks on the OS default socket timeout
            # (can be minutes) against a black-holed broker, which would
            # wedge an upload-worker thread. Bound it with a quick TCP
            # reachability probe first so we fail fast instead.
            try:
                socket.create_connection((self.host, self.port), timeout=self.timeout).close()
            except OSError as exc:
                raise UploadError(
                    f"mqtt broker unreachable at {self.host}:{self.port} "
                    f"within {self.timeout:.0f}s: {exc}"
                ) from exc
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
