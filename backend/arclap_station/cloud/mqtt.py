"""MQTT telemetry publisher (§12.5.2 control plane).

Publishes a periodic heartbeat + selected audit events to the Arclap
fleet broker so the Admin Cockpit's map view can show real-time station
state. mTLS auth using the cert pair written by pairing.pair().

Topology (per §12.5.2):
    stations/<serial>/heartbeat        once / 30s
    stations/<serial>/audit            on every audit_emit
    stations/<serial>/cmd              broker -> station (commands)

Disabled when station.json paired != true OR cert files missing —
no exceptions thrown, just a no-op singleton. That keeps the dev /
standalone deployment path identical to today's.

This module is intentionally lazy: it only `import paho.mqtt` when
start() is called. So a Pi that's never paired never pulls in the lib.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arclap_station.config import get_settings
from arclap_station.station_config import get_station_store

log = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SEC = 30.0
RECONNECT_BACKOFF_SEC = (5.0, 15.0, 60.0, 300.0)  # 5s → 5min then steady


class MqttPublisher:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._client: Any | None = None
        self._connected = False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if not self._enabled():
            log.info("mqtt disabled (not paired or cert missing)")
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="arclap-mqtt", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None

    def connected(self) -> bool:
        return self._connected

    # ----- internal --------------------------------------------------------

    def _enabled(self) -> bool:
        cfg = get_station_store().load()
        if not cfg.paired:
            return False
        cert_dir = get_settings().paths.etc / "iot"
        return (
            (cert_dir / "device.crt").exists()
            and (cert_dir / "device.key").exists()
        )

    def _broker_host(self) -> str | None:
        try:
            store_path = get_settings().paths.etc / "station.json"
            if store_path.exists():
                d = json.loads(store_path.read_text())
                return d.get("broker")
        except (OSError, json.JSONDecodeError):
            pass
        return None

    def _serial(self) -> str:
        return get_station_store().load().serial or "unknown"

    def _run(self) -> None:
        try:
            import paho.mqtt.client as mqtt  # noqa: PLC0415
        except ImportError:
            log.warning("paho-mqtt not installed — mqtt publisher disabled")
            return

        broker = self._broker_host()
        if not broker:
            log.warning("no broker configured — mqtt publisher exiting")
            return
        cert_dir = get_settings().paths.etc / "iot"

        backoff_idx = 0
        while not self._stop.is_set():
            try:
                client = mqtt.Client(client_id=f"arclap-{self._serial()}")
                client.tls_set(
                    certfile=str(cert_dir / "device.crt"),
                    keyfile=str(cert_dir / "device.key"),
                )
                client.on_connect = self._on_connect
                client.on_disconnect = self._on_disconnect
                # AWS IoT Core endpoints listen on 8883 for mTLS.
                client.connect(broker, 8883, keepalive=60)
                self._client = client
                client.loop_start()
                self._heartbeat_loop()
                client.loop_stop()
                client.disconnect()
                self._client = None
            except Exception as exc:  # noqa: BLE001
                log.warning("mqtt session ended: %s", exc)
                self._connected = False
            if self._stop.is_set():
                break
            wait = RECONNECT_BACKOFF_SEC[
                min(backoff_idx, len(RECONNECT_BACKOFF_SEC) - 1)
            ]
            backoff_idx += 1
            if self._stop.wait(timeout=wait):
                break

    def _on_connect(self, client: Any, userdata: Any, flags: Any, rc: int) -> None:  # noqa: ANN401, ARG002
        if rc == 0:
            self._connected = True
            log.info("mqtt connected to %s", self._broker_host())
            client.subscribe(f"stations/{self._serial()}/cmd")
        else:
            log.warning("mqtt connect rc=%s", rc)

    def _on_disconnect(self, client: Any, userdata: Any, rc: int) -> None:  # noqa: ANN401, ARG002
        self._connected = False
        log.info("mqtt disconnected rc=%s", rc)

    def _heartbeat_loop(self) -> None:
        from arclap_station.telemetry.metrics import snapshot  # noqa: PLC0415

        topic = f"stations/{self._serial()}/heartbeat"
        while not self._stop.is_set() and self._client is not None:
            try:
                payload = json.dumps({
                    "ts": datetime.now(UTC).isoformat(),
                    "metrics": snapshot(),
                })
                self._client.publish(topic, payload, qos=0)
            except Exception as exc:  # noqa: BLE001
                log.debug("mqtt heartbeat publish failed: %s", exc)
            if self._stop.wait(timeout=HEARTBEAT_INTERVAL_SEC):
                return


_publisher: MqttPublisher | None = None
_lock = threading.Lock()


def get_publisher() -> MqttPublisher:
    global _publisher
    with _lock:
        if _publisher is None:
            _publisher = MqttPublisher()
    return _publisher
