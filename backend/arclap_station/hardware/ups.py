"""UPS HAT reader + safe-shutdown trigger — graceful no-op without one.

Construction sites have dirty power. A yanked feed mid-write corrupts
the SD card — the #1 silent Pi field-death. A UPS HAT bridges the gap;
this module reads its state so the station can (a) surface it in health
and (b) shut down cleanly before the battery dies rather than losing
power abruptly.

Detection is best-effort across the two portable mechanisms, in order:

  1. Linux power_supply class — many HATs (PiJuice, some Geekworm)
     register a battery under /sys/class/power_supply/. Zero deps.
  2. INA219 over I2C — the common voltage/current monitor on Waveshare /
     Geekworm X-series HATs. Needs `smbus2`; if absent we skip silently.

If neither is present we report `present: False` and the station treats
it as a normal wired install.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_PSU_ROOT = Path("/sys/class/power_supply")
# Common INA219 I2C addresses on Pi UPS HATs.
_INA219_ADDRS = (0x40, 0x41, 0x42, 0x43, 0x36)
_I2C_BUS = 1


def _read_text(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _from_power_supply() -> dict[str, Any] | None:
    """Read a battery via the kernel power_supply class. Returns None if
    no battery-type supply is registered."""
    if not _PSU_ROOT.exists():
        return None
    try:
        for entry in _PSU_ROOT.iterdir():
            kind = _read_text(entry / "type")
            if kind != "Battery":
                continue
            cap = _read_text(entry / "capacity")
            status = (_read_text(entry / "status") or "").lower()
            percent = int(cap) if cap and cap.isdigit() else None
            # status: Charging / Full / Not charging => on mains;
            # Discharging => on battery.
            on_battery = status == "discharging"
            return {
                "present": True,
                "source": f"power_supply/{entry.name}",
                "percent": percent,
                "on_battery": on_battery,
                "status": status or None,
            }
    except OSError:
        return None
    return None


def _from_ina219() -> dict[str, Any] | None:
    """Read battery voltage via an INA219 over I2C and derive an approximate
    percent (Li-ion 3.0–4.2V mapped to 0–100). Best-effort; needs smbus2."""
    try:
        from smbus2 import SMBus  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - smbus2 not installed → no INA219 path
        return None

    for addr in _INA219_ADDRS:
        try:
            with SMBus(_I2C_BUS) as bus:
                # INA219 bus-voltage register (0x02): bits 15..3 are the
                # voltage in 4 mV LSBs after a >>3 shift.
                raw = bus.read_word_data(addr, 0x02)
                # word comes back byte-swapped on the Pi's SMBus.
                swapped = ((raw << 8) & 0xFF00) | (raw >> 8)
                voltage = (swapped >> 3) * 0.004
                if voltage < 2.5 or voltage > 14.0:
                    continue  # not a plausible battery reading at this addr
                # Map a single Li-ion cell (3.0–4.2 V) to 0–100%.
                percent = max(0, min(100, round((voltage - 3.0) / (4.2 - 3.0) * 100)))
                return {
                    "present": True,
                    "source": f"ina219@0x{addr:02x}",
                    "percent": percent,
                    "voltage": round(voltage, 2),
                    # INA219 alone can't tell mains-vs-battery; report unknown.
                    "on_battery": None,
                    "status": None,
                }
        except Exception:  # noqa: BLE001 - try the next address
            continue
    return None


def read_ups() -> dict[str, Any]:
    """Return the current UPS state, or {'present': False} on a bare Pi."""
    for reader in (_from_power_supply, _from_ina219):
        try:
            res = reader()
            if res:
                return res
        except Exception as exc:  # noqa: BLE001
            log.debug("ups reader %s failed: %s", reader.__name__, exc)
    return {"present": False}


# Below this battery % while on battery power, trigger a clean shutdown.
SAFE_SHUTDOWN_PERCENT = 12


def maybe_safe_shutdown() -> bool:
    """If on battery and critically low, audit + initiate a clean shutdown.

    Returns True if a shutdown was triggered. Called from the periodic
    health loop. Uses `systemctl poweroff` (allowed for the arclap user
    via the existing polkit rules) so buffers flush and the SD card
    isn't left mid-write.
    """
    ups = read_ups()
    if not ups.get("present"):
        return False
    if not ups.get("on_battery"):
        return False
    pct = ups.get("percent")
    if pct is None or pct > SAFE_SHUTDOWN_PERCENT:
        return False

    log.error("UPS critically low (%s%%) on battery — initiating safe shutdown", pct)
    try:
        from arclap_station.audit import emit as audit_emit  # noqa: PLC0415

        audit_emit("system", "power.safe_shutdown", {"percent": pct, "source": ups.get("source")})
    except Exception:  # noqa: BLE001
        pass
    try:
        import subprocess  # noqa: PLC0415

        subprocess.run(["systemctl", "poweroff"], timeout=10, capture_output=True)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("safe shutdown failed: %s", exc)
        return False
