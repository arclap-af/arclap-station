"""Cloud integration — AWS IoT pairing + MQTT telemetry publisher.

This subpackage owns the Pi side of the §12.5.3 zero-touch provisioning
flow and the §12.5.2 MQTT telemetry channel. It's split out of the API
package so the cockpit can expose pairing controls without dragging the
mqtt_client dependency into the auth path.

The MQTT publisher is implemented as a daemon thread that owns its own
reconnect loop. Pairing state and broker config live in station.json so
they survive reboots without an extra DB schema.
"""
