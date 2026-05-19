"""Out-of-process watchdogs invoked by systemd timers.

Each module here exposes a `run()` entrypoint that performs one probe and
exits with a meaningful status code. Designed to be invoked from a systemd
oneshot service so we never risk a long-running supervisor inside the main
FastAPI process.
"""
