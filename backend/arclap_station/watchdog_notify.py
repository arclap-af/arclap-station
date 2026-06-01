"""Minimal, dependency-free sd_notify for the systemd software watchdog.

systemd's `Restart=always` recovers a service that *crashes*. It does
NOT recover one that *hangs* — a deadlocked event loop, a wedged
libgphoto2 call holding the GIL, a stuck network read. To the service
manager the process is still alive, so the station goes dark and stays
dark until a human notices. That is the exact "cockpit works then
doesn't, and stays dead" failure class.

The fix: the unit declares `WatchdogSec=N`, and the app sends
`WATCHDOG=1` to systemd faster than every N seconds from inside the
asyncio event loop. If the loop hangs, the pings stop, and systemd
kills + restarts the service automatically.

This module speaks the sd_notify protocol over the `$NOTIFY_SOCKET`
unix datagram socket directly — no `systemd-python` dependency (which
isn't always present in the venv). When `$NOTIFY_SOCKET` is unset
(running under `npm run dev`, pytest, or any non-systemd context) every
call is a safe no-op, so the same code runs identically off-Pi.
"""

from __future__ import annotations

import logging
import os
import socket

log = logging.getLogger(__name__)


def _notify(state: str) -> bool:
    """Send a single sd_notify datagram. Returns False (no-op) when not
    running under systemd or on any socket error — never raises."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    # Abstract-namespace sockets start with '@' in the env var; the
    # kernel wants a leading NUL byte instead.
    if addr[0] == "@":
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as sock:
            sock.connect(addr)
            sock.sendall(state.encode("utf-8"))
        return True
    except OSError as exc:
        log.debug("sd_notify(%r) failed: %s", state, exc)
        return False


def notify_ready() -> bool:
    """Tell systemd the service finished starting. Harmless under
    Type=simple; required if the unit is ever switched to Type=notify."""
    return _notify("READY=1")


def notify_watchdog() -> bool:
    """Pet the watchdog. Call this faster than the unit's WatchdogSec."""
    return _notify("WATCHDOG=1")


def watchdog_interval_seconds() -> float | None:
    """Half of the unit's WatchdogSec (the recommended ping cadence), or
    None if the watchdog isn't enabled for this run.

    systemd exports WATCHDOG_USEC (microseconds) to the service when
    WatchdogSec is set. We ping at half that so a single missed beat
    (GC pause, slow disk) doesn't trip a false restart.
    """
    raw = os.environ.get("WATCHDOG_USEC")
    if not raw:
        return None
    try:
        usec = int(raw)
    except ValueError:
        return None
    if usec <= 0:
        return None
    return (usec / 1_000_000.0) / 2.0
