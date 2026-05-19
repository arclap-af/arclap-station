"""Camera USB watchdog.

Periodically probes whether the connected DSLR is healthy at the libgphoto2
level. After N consecutive failures, performs a sysfs USB reauthorize on
the camera's bus device to recover a stuck PTP session. Bounded retry
count prevents reset loops; an audit event fires when we give up.

The probe is deliberately lightweight: `gphoto2 --auto-detect` enumerates
USB-connected cameras without opening a PTP session, so it does not
interfere with the running backend's session.

CLI: `arclap-station camera-watchdog`
Exit codes:
    0 = healthy (or no camera attached)
    1 = unhealthy but fail-count still below threshold
    2 = unhealthy, just performed USB reset
    3 = unhealthy, exhausted reset budget — escalated via audit log
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arclap_station.audit import emit as audit_emit
from arclap_station.config import get_settings

log = logging.getLogger(__name__)

# DSLR USB vendor IDs we watch over. Add new bodies here as we support them.
DSLR_VENDOR_IDS = {
    "04a9",  # Canon
    "04b0",  # Nikon
    "054c",  # Sony
    "04cb",  # Fujifilm
}

# Failures in a row before we attempt a USB reset.
FAIL_THRESHOLD = 3
# Maximum USB resets before we stop trying (avoid reset storms).
MAX_RESETS_IN_A_ROW = 2
# Path of the persistent state file (small JSON) under the var dir.
STATE_FILENAME = "camera_watchdog.json"


class CameraWatchdog:
    def __init__(self) -> None:
        settings = get_settings()
        self.state_path: Path = settings.paths.var / STATE_FILENAME

    # ------- state ----------------------------------------------------------

    def _load_state(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {
                "fail_count": 0,
                "reset_count": 0,
                "last_reset_at": None,
                "last_ok_at": None,
            }

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(self.state_path)

    # ------- probes ---------------------------------------------------------

    def _camera_enumerated(self) -> bool:
        """True if any DSLR USB device is visible in /sys/bus/usb/devices/."""
        usb_root = Path("/sys/bus/usb/devices")
        if not usb_root.exists():
            return False
        for d in usb_root.iterdir():
            vid_file = d / "idVendor"
            if not vid_file.is_file():
                continue
            try:
                vid = vid_file.read_text().strip().lower()
            except OSError:
                continue
            if vid in DSLR_VENDOR_IDS:
                return True
        return False

    def _gphoto_responsive(self, timeout: float = 5.0) -> bool:
        """True if `gphoto2 --auto-detect` returns at least one camera row.

        --auto-detect is deliberately chosen because it enumerates USB
        devices that match libgphoto2's camera list but does NOT open a
        PTP session — so it won't fight the backend's already-open handle.
        """
        try:
            result = subprocess.run(
                ["gphoto2", "--auto-detect"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        if result.returncode != 0:
            return False
        # A successful row looks like:
        #   Canon EOS 5D Mark IV           usb:002,007
        return bool(re.search(r"\busb:\d{3},\d{3}\b", result.stdout))

    # ------- recovery -------------------------------------------------------

    def _attempt_usb_reset(self) -> tuple[bool, list[str]]:
        """Toggle each DSLR USB device's `authorized` sysfs flag (0→1).

        Returns (ok, list of touched device paths). Requires root or a
        writable sysfs — systemd timer runs us as root for this reason.
        """
        touched: list[str] = []
        usb_root = Path("/sys/bus/usb/devices")
        if not usb_root.exists():
            return False, touched
        for d in usb_root.iterdir():
            vid_file = d / "idVendor"
            auth_file = d / "authorized"
            if not vid_file.is_file() or not auth_file.is_file():
                continue
            try:
                vid = vid_file.read_text().strip().lower()
            except OSError:
                continue
            if vid not in DSLR_VENDOR_IDS:
                continue
            try:
                auth_file.write_text("0")
                time.sleep(0.75)
                auth_file.write_text("1")
                touched.append(d.name)
            except OSError as exc:
                log.warning("USB reset failed at %s: %s", d.name, exc)
                return False, touched
        return bool(touched), touched

    # ------- entrypoint -----------------------------------------------------

    def probe_once(self) -> int:
        state = self._load_state()
        now_iso = datetime.now(UTC).isoformat()

        if not self._camera_enumerated():
            # No DSLR plugged in. Reset counters so a future re-plug starts clean.
            if state["fail_count"] > 0 or state["reset_count"] > 0:
                _safe_audit("camera.watchdog_unattached", {})
            state.update(fail_count=0, reset_count=0)
            self._save_state(state)
            return 0

        if self._gphoto_responsive():
            if state["fail_count"] > 0:
                _safe_audit(
                    "camera.recovered",
                    {
                        "after_fails": state["fail_count"],
                        "resets": state["reset_count"],
                    },
                )
            state.update(fail_count=0, reset_count=0, last_ok_at=now_iso)
            self._save_state(state)
            return 0

        # Unhealthy probe.
        state["fail_count"] += 1
        self._save_state(state)
        log.warning(
            "watchdog probe failed (fail_count=%d, reset_count=%d)",
            state["fail_count"],
            state["reset_count"],
        )

        if state["fail_count"] < FAIL_THRESHOLD:
            return 1

        if state["reset_count"] >= MAX_RESETS_IN_A_ROW:
            _safe_audit(
                "camera.watchdog_giving_up",
                {
                    "fail_count": state["fail_count"],
                    "resets": state["reset_count"],
                },
            )
            return 3

        ok, touched = self._attempt_usb_reset()
        state.update(
            fail_count=0,  # next probe assesses whether reset worked
            reset_count=state["reset_count"] + 1,
            last_reset_at=now_iso,
        )
        self._save_state(state)
        _safe_audit(
            "camera.watchdog_reset",
            {"ok": ok, "devices": touched, "reset_count": state["reset_count"]},
        )
        return 2


def _safe_audit(event: str, details: dict[str, Any]) -> None:
    """Audit emit, but never let it crash the watchdog (DB might be locked)."""
    try:
        audit_emit("system", event, details)
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_emit('%s') failed: %s", event, exc)


def run() -> int:
    """Entrypoint invoked from the CLI / systemd oneshot."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    try:
        return CameraWatchdog().probe_once()
    except Exception as exc:  # noqa: BLE001
        log.exception("watchdog crashed: %s", exc)
        # Don't let a watchdog crash kill the timer cadence.
        return 0
