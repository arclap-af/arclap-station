"""WireGuard support tunnel client (§12.5.6).

The deployment model:
  - Each Pi has a long-lived WG keypair (generated at install time,
    written to /etc/arclap/wg/).
  - Arclap operates a bastion server with a public endpoint. Each Pi's
    public key is registered with the bastion at warehouse pre-pair.
  - The Pi runs `wg-quick up arclap-tunnel` on-demand: cockpit toggles
    enable/disable; audit log records every state change.
  - Tunnel is OUTBOUND ONLY — Pi initiates, bastion never has the Pi's
    routable address. Matches the §12.5.5 outbound-only security model.
  - When up, Arclap operators can SSH from the bastion to the Pi's
    tunnel IP. No inbound ports opened on the Pi.

API surface (this module):
  - status()      → is wg-quick up? what's the tunnel IP / handshake age?
  - up()          → bring tunnel up, returns peer state
  - down()        → bring tunnel down
  - rotate_key()  → regenerate keypair (operator-initiated)

The config file is bootstrapped by install.sh; this module never
generates keys at runtime to avoid breaking peer-registration assumptions
on the bastion.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from arclap_station.audit import emit as audit_emit
from arclap_station.config import get_settings

log = logging.getLogger(__name__)

INTERFACE_NAME = "arclap-tunnel"


def _config_path() -> Path:
    return get_settings().paths.etc / "wg" / f"{INTERFACE_NAME}.conf"


def _have_wireguard() -> bool:
    return any(
        Path(p).exists()
        for p in ("/usr/bin/wg-quick", "/usr/sbin/wg-quick", "/usr/local/bin/wg-quick")
    )


def status() -> dict[str, Any]:
    """Return tunnel state — never raises.

    Shape:
        {
          "installed": bool,        # wireguard tools on the box
          "configured": bool,       # config file present
          "up": bool,               # interface up
          "peer": {pubkey,endpoint,latest_handshake_sec,rx_bytes,tx_bytes},
          "address": str|null,      # tunnel IP
        }
    """
    out: dict[str, Any] = {
        "installed": _have_wireguard(),
        "configured": _config_path().exists(),
        "up": False,
        "peer": None,
        "address": None,
    }
    if not out["installed"]:
        return out
    try:
        r = subprocess.run(
            ["wg", "show", INTERFACE_NAME, "dump"],
            capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return out
    if r.returncode != 0:
        return out
    out["up"] = True
    lines = r.stdout.strip().splitlines()
    # First line: interface (private_key, public_key, listen_port, fwmark)
    # Subsequent lines: peer (pub, psk, endpoint, allowed_ips, latest_handshake,
    #   rx_bytes, tx_bytes, persistent_keepalive)
    if len(lines) >= 2:
        parts = lines[1].split("\t")
        if len(parts) >= 7:
            try:
                hs = int(parts[4])
            except (ValueError, IndexError):
                hs = 0
            try:
                rx = int(parts[5]); tx = int(parts[6])
            except (ValueError, IndexError):
                rx = tx = 0
            import time as _t  # noqa: PLC0415

            out["peer"] = {
                "pubkey": parts[0][:16] + "…",
                "endpoint": parts[2] if parts[2] != "(none)" else None,
                "latest_handshake_age_sec":
                    int(_t.time() - hs) if hs > 0 else None,
                "rx_bytes": rx,
                "tx_bytes": tx,
            }
    # Tunnel IP — read from `ip -br addr show dev <iface>`.
    try:
        r2 = subprocess.run(
            ["ip", "-br", "addr", "show", "dev", INTERFACE_NAME],
            capture_output=True, text=True, timeout=2,
        )
        if r2.returncode == 0:
            parts = r2.stdout.split()
            if len(parts) >= 3:
                out["address"] = parts[2].split("/")[0]
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return out


def up() -> dict[str, Any]:
    """Bring the support tunnel up. Audited."""
    if not _have_wireguard():
        return {"ok": False, "error": "wireguard not installed"}
    if not _config_path().exists():
        return {"ok": False, "error": "tunnel config not provisioned"}
    try:
        r = subprocess.run(
            ["wg-quick", "up", INTERFACE_NAME],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        audit_emit("user", "tunnel.up_failed", {"error": str(exc)[:200]})
        return {"ok": False, "error": str(exc)}
    if r.returncode != 0:
        # wg-quick prints "RTNETLINK answers: File exists" if already up.
        if "exists" in r.stderr.lower():
            return {"ok": True, "already_up": True, **status()}
        audit_emit("user", "tunnel.up_failed", {"error": r.stderr[:200]})
        return {"ok": False, "error": r.stderr.strip()[:200]}
    audit_emit("user", "tunnel.up", {})
    return {"ok": True, **status()}


def down() -> dict[str, Any]:
    if not _have_wireguard():
        return {"ok": False, "error": "wireguard not installed"}
    try:
        r = subprocess.run(
            ["wg-quick", "down", INTERFACE_NAME],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        audit_emit("user", "tunnel.down_failed", {"error": str(exc)[:200]})
        return {"ok": False, "error": str(exc)}
    if r.returncode != 0 and "does not exist" not in r.stderr.lower():
        audit_emit("user", "tunnel.down_failed", {"error": r.stderr[:200]})
        return {"ok": False, "error": r.stderr.strip()[:200]}
    audit_emit("user", "tunnel.down", {})
    return {"ok": True}
