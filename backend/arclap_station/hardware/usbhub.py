"""Controllable-USB-hub power cycling via `uhubctl` — graceful no-op.

When a Canon DSLR's USB controller wedges (the `-7 I/O problem` we
chased for hours), no software reset recovers it because the fault is
in the device's USB stack, not ours. The only real fix short of a human
unplugging the cable is to cut bus power to the port for a moment and
let the camera cold-start its USB.

That requires a hub whose downstream port power is software-switchable
(e.g. the Pi 5's own root ports on recent firmware, or an external
data-hub with per-port power control), driven by `uhubctl`.

This module is fully optional: if `uhubctl` isn't installed or no
power-switchable hub is present, `power_cycle_usb()` returns False and
the station falls back to its existing recovery ladder
(authorized-toggle → service self-restart). The moment the hardware +
uhubctl are in place, recovery escalates to a real power cycle with no
code change.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time

log = logging.getLogger(__name__)


def uhubctl_available() -> bool:
    return shutil.which("uhubctl") is not None


def list_switchable() -> str | None:
    """Return uhubctl's device listing, or None if unavailable/none found."""
    if not uhubctl_available():
        return None
    try:
        out = subprocess.run(["uhubctl"], capture_output=True, text=True, timeout=8)
        return out.stdout.strip() or None
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        log.debug("uhubctl list failed: %s", exc)
        return None


def power_cycle_usb(off_seconds: float = 2.0) -> bool:
    """Cut + restore power to switchable downstream USB ports.

    Best-effort and conservative: cycles all power-switchable ports
    uhubctl can see (the camera is the only powered downstream device
    on a typical station, so this is safe). Returns True only if both
    the off and on commands ran without error.

    Audits the action so a power-cycle shows up in the Activity feed —
    operators need to know the station physically reset the camera bus.
    """
    if not uhubctl_available():
        return False
    try:
        off = subprocess.run(
            ["uhubctl", "--action", "off", "--ports", "2"],
            capture_output=True, text=True, timeout=10,
        )
        # If "--ports 2" doesn't match this hub, fall back to all ports.
        if off.returncode != 0:
            off = subprocess.run(
                ["uhubctl", "--action", "off"],
                capture_output=True, text=True, timeout=10,
            )
        if off.returncode != 0:
            log.info("uhubctl power-off failed: %s", off.stderr.strip()[:200])
            return False
        time.sleep(max(0.5, off_seconds))
        on = subprocess.run(
            ["uhubctl", "--action", "on"],
            capture_output=True, text=True, timeout=10,
        )
        ok = on.returncode == 0
        if ok:
            log.warning("USB bus power-cycled via uhubctl to recover the camera")
            try:
                from arclap_station.audit import emit as audit_emit  # noqa: PLC0415

                audit_emit("system", "camera.usb_power_cycle", {"off_seconds": off_seconds})
            except Exception:  # noqa: BLE001
                pass
        return ok
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        log.info("uhubctl power-cycle errored: %s", exc)
        return False
