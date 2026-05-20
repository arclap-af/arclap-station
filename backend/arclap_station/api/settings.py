"""Settings router: /api/settings/*."""

from __future__ import annotations

import json
import platform
import socket
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from arclap_station.api.deps import require_session
from arclap_station.audit import emit as audit_emit
from arclap_station.audit import recent as recent_audit
from arclap_station.audit import verify_chain
from arclap_station.config import get_settings
from arclap_station.station_config import StationConfig, get_station_store
from arclap_station.telemetry.logs import follow_journal, recent_journal
from arclap_station.telemetry.metrics import snapshot
from arclap_station.terminal.pty import info as pty_info

router = APIRouter(prefix="/api/settings", tags=["settings"])


class GeneralUpdateRequest(BaseModel):
    name: str | None = None
    timezone: str | None = None
    lat: float | None = None
    lon: float | None = None
    # v0.8 fields — match StationConfig dataclass.
    site: str | None = None
    watermark: bool | None = None
    dedup_threshold: int | None = None
    bandwidth_kbps: int | None = None
    project_starts_at: str | None = None
    project_ends_at: str | None = None


@router.get("/general")
async def get_general(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    cfg = get_station_store().load()
    return cfg.to_dict()


@router.put("/general")
async def update_general(
    payload: GeneralUpdateRequest,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    fields = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    cfg = get_station_store().update(**fields)
    audit_emit("user", "settings.general.update", fields)
    return cfg.to_dict()


@router.get("/network")
async def network_info(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    """Real interface state from psutil + sysfs (Pi 5 specific).

    Returns a structured payload covering ethernet, wifi (if present),
    cellular (if present), and a list of connectivity probes.
    """
    import psutil  # noqa: PLC0415

    hostname = socket.gethostname()
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()

    def _iface(name: str) -> dict[str, Any]:
        if name not in addrs:
            return {"connected": False, "interface": name}
        ipv4 = ""
        mac = ""
        for a in addrs[name]:
            if a.family.name == "AF_INET":
                ipv4 = a.address
            elif a.family.name == "AF_PACKET":
                mac = a.address
        is_up = bool(stats.get(name) and stats[name].isup)
        return {
            "connected": is_up and bool(ipv4),
            "interface": name,
            "mode": "DHCP",  # NetworkManager state would refine this
            "ipv4": ipv4 or "—",
            "gateway": _default_gateway() or "—",
            "dns": _primary_dns() or "—",
            "mac": mac or "—",
        }

    # Pick the first wired interface that looks like eth* / enp* / en* — Pi 5
    # is usually 'eth0' on Ubuntu, but the predictable-name code path can
    # produce different names.
    eth_name = next(
        (n for n in addrs if n.startswith(("eth", "enp", "en"))), "eth0"
    )
    wifi_name = next(
        (n for n in addrs if n.startswith(("wlan", "wlp", "wlx"))), "wlan0"
    )
    eth = _iface(eth_name)
    wifi = _iface(wifi_name) if wifi_name in addrs else {"connected": False, "interface": "wlan0"}

    # SSID + signal (best-effort via `iw dev`).
    ssid, signal_dbm, band = "—", None, "—"
    if wifi.get("connected"):
        ssid, signal_dbm, band = _wifi_details(wifi["interface"])

    return {
        "hostname": hostname,
        "ip": eth.get("ipv4") or _local_ip(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "ethernet": eth,
        "wifi": {
            "connected": wifi.get("connected", False),
            "ssid": ssid,
            "security": "—",
            "band": band,
            "signal_dbm": signal_dbm,
            "interface": wifi.get("interface", "wlan0"),
        },
        "cellular": {
            "status": "absent",
            "modem": "—",
            "carrier": "—",
            "signal_dbm": None,
            "apn": "—",
            "data_mb": 0,
        },
        "probes": _connectivity_probes(),
    }


def _local_ip() -> str:
    """Return the LAN IP by opening a UDP socket to a public address.

    We never actually send anything; the OS picks the routing-source IP
    for us. Works without network because we don't connect.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("1.1.1.1", 1))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def _default_gateway() -> str:
    try:
        with open("/proc/net/route") as f:
            next(f)  # header
            for line in f:
                fields = line.split()
                if len(fields) >= 3 and fields[1] == "00000000":
                    return ".".join(str(int(fields[2][i : i + 2], 16)) for i in (6, 4, 2, 0))
    except OSError:
        pass
    return ""


def _primary_dns() -> str:
    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                line = line.strip()
                if line.startswith("nameserver"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return parts[1]
    except OSError:
        pass
    return ""


def _wifi_details(iface: str) -> tuple[str, int | None, str]:
    """SSID + signal dBm + band, via `iw dev <iface> link`."""
    import subprocess  # noqa: PLC0415

    try:
        out = subprocess.run(
            ["iw", "dev", iface, "link"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return "—", None, "—"
    if out.returncode != 0:
        return "—", None, "—"
    ssid = "—"
    signal: int | None = None
    band = "—"
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.startswith("SSID:"):
            ssid = line.split(":", 1)[1].strip() or "—"
        elif line.startswith("signal:"):
            try:
                signal = int(line.split()[1])
            except (ValueError, IndexError):
                pass
        elif line.startswith("freq:"):
            try:
                freq = int(line.split()[1])
                band = "2.4 GHz" if freq < 3000 else "5 GHz" if freq < 6000 else "6 GHz"
            except (ValueError, IndexError):
                pass
    return ssid, signal, band


def _connectivity_probes() -> list[dict[str, str]]:
    """Quick reachability checks for the cockpit's Network tab."""
    import subprocess  # noqa: PLC0415

    results: list[dict[str, str]] = []

    def add(label: str, ok: bool, detail: str = "") -> None:
        results.append({"label": label, "result": detail or ("ok" if ok else "down"), "level": "ok" if ok else "bad"})

    # Default gateway reachable?
    gw = _default_gateway()
    if gw:
        try:
            r = subprocess.run(
                ["ping", "-c", "1", "-W", "1", gw],
                capture_output=True,
                timeout=3,
            )
            add(f"Gateway {gw}", r.returncode == 0)
        except (FileNotFoundError, subprocess.SubprocessError):
            add(f"Gateway {gw}", False, "ping not available")
    # Internet
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-W", "2", "1.1.1.1"],
            capture_output=True,
            timeout=4,
        )
        add("Internet (1.1.1.1)", r.returncode == 0)
    except (FileNotFoundError, subprocess.SubprocessError):
        add("Internet (1.1.1.1)", False, "ping not available")
    # DNS
    try:
        socket.gethostbyname("cloudflare.com")
        add("DNS resolve", True)
    except OSError as exc:
        add("DNS resolve", False, str(exc)[:40])
    # NTP synced?
    try:
        r = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        synced = r.stdout.strip() == "yes"
        add("NTP synced", synced, "yes" if synced else "no")
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    # Captive portal detection — hit a known sentinel URL and confirm
    # we get the expected payload back. Most public WiFi hijacks 80
    # to a login page, which would return HTML 200 with different body.
    portal_state = _captive_portal_probe()
    if portal_state == "ok":
        add("No captive portal", True, "direct")
    elif portal_state == "portal":
        results.append({
            "label": "Captive portal",
            "result": "blocking outbound traffic",
            "level": "warn",
        })
        try:
            audit_emit("system", "network.captive_portal_detected", {})
        except Exception:  # noqa: BLE001
            pass
    return results


def _captive_portal_probe() -> str:
    """Returns 'ok', 'portal', or 'unknown'.

    Uses Cloudflare's tiny generate_204 endpoint — by convention a
    successful HTTPS-bypassing intermediate returns 204 No Content with
    an empty body. A captive portal hijack returns a 200 HTML page.
    """
    import urllib.request  # noqa: PLC0415

    url = "http://cp.cloudflare.com/generate_204"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "arclap-station/captive-probe"})
        with urllib.request.urlopen(req, timeout=3) as resp:  # noqa: S310 (literal URL)
            if resp.status == 204 and not resp.read(8):
                return "ok"
            return "portal"
    except (urllib.error.URLError, OSError, TimeoutError):
        return "unknown"


# ----- WiFi scan + connect (Tier 3) --------------------------------------
#
# We use NetworkManager (`nmcli`) because it's the supported Ubuntu 26.04
# default and handles WPA2/3 / hidden SSIDs / auto-reconnect for us. The
# previous "iw" path is read-only; nmcli is read-write.
#
# Security: nmcli stores PSKs in /etc/NetworkManager/system-connections
# with mode 0600 owned by root. We never log the PSK and we never expose
# it via the API — readback is intentionally one-way.


@router.get("/network/wifi-scan")
async def wifi_scan(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    """Return visible WiFi access points, ordered by signal strength.

    Uses `nmcli -t -f SSID,SIGNAL,SECURITY,FREQ device wifi list`. Triggers
    a rescan first so the user sees fresh APs after walking around the site.
    """
    import subprocess  # noqa: PLC0415

    try:
        subprocess.run(
            ["nmcli", "device", "wifi", "rescan"],
            capture_output=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return {"ok": False, "error": "nmcli not available", "networks": []}
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,FREQ,IN-USE",
             "device", "wifi", "list"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc), "networks": []}
    networks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in out.stdout.splitlines():
        # nmcli -t output is colon-separated; SSIDs may contain
        # escaped colons (\:) — we want to preserve those.
        parts: list[str] = []
        buf = ""
        i = 0
        while i < len(line):
            if line[i] == "\\" and i + 1 < len(line):
                buf += line[i + 1]
                i += 2
            elif line[i] == ":":
                parts.append(buf)
                buf = ""
                i += 1
            else:
                buf += line[i]
                i += 1
        parts.append(buf)
        if len(parts) < 4:
            continue
        ssid, signal, security, freq = parts[0], parts[1], parts[2], parts[3]
        in_use = parts[4] if len(parts) >= 5 else ""
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        try:
            sig = int(signal)
        except ValueError:
            sig = 0
        try:
            freq_mhz = int(freq)
        except ValueError:
            freq_mhz = 0
        band = (
            "2.4 GHz" if freq_mhz < 3000
            else "5 GHz" if freq_mhz < 6000
            else "6 GHz"
        )
        networks.append({
            "ssid": ssid,
            "signal": sig,
            "security": security or "OPEN",
            "band": band,
            "in_use": bool(in_use.strip() == "*"),
        })
    networks.sort(key=lambda n: -n["signal"])
    return {"ok": True, "networks": networks}


class WifiConnectRequest(BaseModel):
    ssid: str
    psk: str | None = None
    hidden: bool = False


@router.post("/network/wifi-connect")
async def wifi_connect(
    payload: WifiConnectRequest,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """Connect (or replace existing connection) to a WiFi network.

    nmcli stores the PSK encrypted under /etc/NetworkManager — we never
    persist it server-side and never log it. Audit emit logs only the
    SSID, never the secret.
    """
    import subprocess  # noqa: PLC0415

    ssid = payload.ssid.strip()
    if not ssid:
        raise HTTPException(status_code=400, detail="ssid required")
    cmd = ["nmcli", "device", "wifi", "connect", ssid]
    if payload.psk:
        cmd += ["password", payload.psk]
    if payload.hidden:
        cmd += ["hidden", "yes"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        audit_emit("user", "network.wifi_connect_error", {"ssid": ssid, "err": str(exc)})
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if out.returncode != 0:
        # Strip the password echo from stderr if nmcli accidentally
        # included it — never log secrets.
        err = (out.stderr or out.stdout).strip()
        audit_emit("user", "network.wifi_connect_failed", {"ssid": ssid, "err": err[:200]})
        raise HTTPException(status_code=400, detail=err or "connect failed")
    audit_emit("user", "network.wifi_connected", {"ssid": ssid, "hidden": payload.hidden})
    return {"ok": True, "ssid": ssid}


@router.post("/network/wifi-forget")
async def wifi_forget(
    payload: WifiConnectRequest,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """Delete a stored WiFi profile so it won't auto-reconnect."""
    import subprocess  # noqa: PLC0415

    ssid = payload.ssid.strip()
    if not ssid:
        raise HTTPException(status_code=400, detail="ssid required")
    try:
        out = subprocess.run(
            ["nmcli", "connection", "delete", ssid],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if out.returncode != 0:
        raise HTTPException(status_code=400, detail=(out.stderr or "delete failed").strip())
    audit_emit("user", "network.wifi_forget", {"ssid": ssid})
    return {"ok": True}


# ----- Editable network settings (v0.9) ----------------------------------
#
# Everything that operators historically had to SSH in for: ethernet IP
# config (DHCP vs static), hostname, DNS, NTP. All gated by polkit so the
# unprivileged `arclap` user can invoke nmcli/hostnamectl from the API
# without sudo. See install.sh §10 for the polkit rule.


class EthernetConfigRequest(BaseModel):
    """Patch the eth0 (or named) connection's IPv4 config.

    mode='dhcp' clears the static fields. mode='static' requires `address`
    in CIDR form (e.g. '192.168.10.50/24') and at least a `gateway`.
    `dns` is a comma-separated list of resolvers — empty string clears
    overrides and falls back to whatever the gateway advertises.
    """
    interface: str = "eth0"
    mode: str = Field(..., pattern=r"^(dhcp|static)$")
    address: str | None = None  # CIDR, e.g. '192.168.10.50/24'
    gateway: str | None = None
    dns: str | None = None  # comma-separated


@router.post("/network/ethernet")
async def configure_ethernet(
    payload: EthernetConfigRequest,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """Set eth0 to DHCP or static IP. Persistent across reboots via NM."""
    import re  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    iface = payload.interface.strip()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,15}", iface):
        raise HTTPException(status_code=400, detail="invalid interface name")

    # nmcli operates on a "connection profile" — for the wired interface
    # this is usually called "Wired connection 1" or the profile name.
    # We resolve it from `nmcli -t -f NAME,DEVICE connection show`.
    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    profile = None
    for line in r.stdout.splitlines():
        # NAME:DEVICE — handle escaped colons
        parts = line.rsplit(":", 1)
        if len(parts) == 2 and parts[1] == iface:
            profile = parts[0].replace("\\:", ":")
            break
    if profile is None:
        raise HTTPException(status_code=404, detail=f"no NetworkManager profile for {iface}")

    cmd = ["nmcli", "connection", "modify", profile]
    if payload.mode == "dhcp":
        cmd += [
            "ipv4.method", "auto",
            "ipv4.addresses", "",
            "ipv4.gateway", "",
            "ipv4.dns", "",
        ]
    else:
        if not payload.address or "/" not in payload.address:
            raise HTTPException(status_code=400, detail="static mode needs address in CIDR form (e.g. 192.168.10.50/24)")
        if not payload.gateway:
            raise HTTPException(status_code=400, detail="static mode needs gateway")
        cmd += [
            "ipv4.method", "manual",
            "ipv4.addresses", payload.address.strip(),
            "ipv4.gateway", payload.gateway.strip(),
        ]
        if payload.dns:
            # nmcli wants space-separated for `ipv4.dns`.
            cmd += ["ipv4.dns", payload.dns.replace(",", " ").strip()]
        else:
            cmd += ["ipv4.dns", ""]

    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if out.returncode != 0:
        audit_emit("user", "network.ethernet_modify_failed", {
            "interface": iface, "mode": payload.mode, "err": out.stderr[:200],
        })
        raise HTTPException(status_code=400, detail=(out.stderr or "modify failed").strip())

    # Apply: bounce the connection so the new config takes effect.
    # NB: if we mis-configured the static IP, the operator may lose
    # access to the cockpit. The frontend should warn before submission.
    try:
        subprocess.run(["nmcli", "connection", "down", profile], capture_output=True, timeout=10)
        subprocess.run(["nmcli", "connection", "up", profile], capture_output=True, timeout=15)
    except subprocess.SubprocessError:
        pass

    audit_emit("user", "network.ethernet_modified", {
        "interface": iface,
        "mode": payload.mode,
        "address": payload.address if payload.mode == "static" else None,
        "gateway": payload.gateway if payload.mode == "static" else None,
    })
    return {"ok": True, "profile": profile, "mode": payload.mode}


class HostnameRequest(BaseModel):
    hostname: str = Field(..., min_length=1, max_length=63, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]$|^[a-zA-Z0-9]$")


@router.post("/network/hostname")
async def set_hostname(
    payload: HostnameRequest,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """Change the system hostname.

    Uses `hostnamectl set-hostname` (polkit-gated for the arclap user).
    Caddy + Avahi still serve the OLD hostname's TLS cert until a
    service restart; the cockpit advises restarting both after a change.
    The new hostname also propagates to mDNS as `<hostname>.local`.
    """
    import subprocess  # noqa: PLC0415

    new_name = payload.hostname.strip()
    try:
        r = subprocess.run(
            ["hostnamectl", "set-hostname", new_name],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if r.returncode != 0:
        audit_emit("user", "network.hostname_change_failed", {"err": r.stderr[:200]})
        raise HTTPException(status_code=400, detail=(r.stderr or "hostnamectl failed").strip())
    audit_emit("user", "network.hostname_changed", {"new": new_name})
    return {
        "ok": True,
        "hostname": new_name,
        "note": "Caddy + Avahi serve the old TLS cert until you restart them from Settings → Diagnostics, or reboot.",
    }


class DnsRequest(BaseModel):
    """System-wide DNS override.

    servers='' clears the override (back to DHCP/fallback resolvers).
    Otherwise: comma-separated IPs, e.g. '1.1.1.1,9.9.9.9'.
    """
    servers: str = ""


@router.post("/network/dns")
async def set_dns(
    payload: DnsRequest,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """Set system-wide DNS resolvers via resolvectl (runtime + persistent).

    Writes a drop-in to /etc/systemd/resolved.conf.d/60-cockpit.conf so
    the change survives reboot, then `resolvectl flush-caches` so
    in-flight lookups see the new servers immediately.
    """
    import subprocess  # noqa: PLC0415
    from pathlib import Path as _P  # noqa: PLC0415

    servers = [s.strip() for s in payload.servers.replace(";", ",").split(",") if s.strip()]
    # Quick validation — every entry must look like an IPv4 or IPv6 literal.
    import re  # noqa: PLC0415

    pat = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$|^[0-9a-fA-F:]+$")
    for s in servers:
        if not pat.match(s):
            raise HTTPException(status_code=400, detail=f"invalid server: {s!r}")

    dropin_dir = _P("/etc/systemd/resolved.conf.d")
    dropin = dropin_dir / "60-cockpit.conf"
    try:
        dropin_dir.mkdir(parents=True, exist_ok=True)
        if servers:
            body = "[Resolve]\nDNS=" + " ".join(servers) + "\n"
            dropin.write_text(body)
        else:
            dropin.unlink(missing_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not write resolved drop-in: {exc}") from exc

    try:
        subprocess.run(["systemctl", "restart", "systemd-resolved"], capture_output=True, timeout=10)
        subprocess.run(["resolvectl", "flush-caches"], capture_output=True, timeout=5)
    except subprocess.SubprocessError:
        pass

    audit_emit("user", "network.dns_changed", {"servers": servers})
    return {"ok": True, "servers": servers}


class NtpRequest(BaseModel):
    """Custom NTP servers. servers='' restores the default fallback ladder."""
    servers: str = ""


@router.post("/network/ntp")
async def set_ntp(
    payload: NtpRequest,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """Set custom NTP servers via timesyncd drop-in.

    Empty servers list restores the 50-arclap.conf fallback ladder
    (time.cloudflare.com → time.google.com → pool.ntp.org).
    """
    import subprocess  # noqa: PLC0415
    from pathlib import Path as _P  # noqa: PLC0415

    servers = [s.strip() for s in payload.servers.replace(";", ",").split(",") if s.strip()]

    dropin_dir = _P("/etc/systemd/timesyncd.conf.d")
    dropin = dropin_dir / "60-cockpit.conf"
    try:
        dropin_dir.mkdir(parents=True, exist_ok=True)
        if servers:
            body = "[Time]\nNTP=" + " ".join(servers) + "\n"
            dropin.write_text(body)
        else:
            dropin.unlink(missing_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not write timesyncd drop-in: {exc}") from exc

    try:
        subprocess.run(["systemctl", "restart", "systemd-timesyncd"], capture_output=True, timeout=10)
    except subprocess.SubprocessError:
        pass

    audit_emit("user", "network.ntp_changed", {"servers": servers})
    return {"ok": True, "servers": servers}


@router.get("/network/connections")
async def list_connections(
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """List all NetworkManager connection profiles (saved networks)."""
    import subprocess  # noqa: PLC0415

    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE,ACTIVE", "connection", "show"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return {"ok": False, "connections": []}
    conns: list[dict[str, Any]] = []
    for line in r.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 5:
            continue
        conns.append({
            "name": parts[0],
            "uuid": parts[1],
            "type": parts[2],
            "device": parts[3] if parts[3] else None,
            "active": parts[4] == "yes",
        })
    return {"ok": True, "connections": conns}


@router.get("/security")
async def security_info(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    chain = verify_chain()
    return {
        "audit_chain": chain,
        "pty": pty_info(),
        "tls": _tls_info(),
        "ssh": _ssh_info(),
        "pin_changed_days_ago": _pin_age_days(),
    }


def _pin_age_days() -> int | None:
    """Days since the PIN file was last modified. None if never set."""
    import time as _time  # noqa: PLC0415
    from pathlib import Path as _P  # noqa: PLC0415

    p = _P(get_settings().paths.etc) / "auth.json"
    if not p.is_file():
        return None
    try:
        age_s = _time.time() - p.stat().st_mtime
        return int(age_s / 86400)
    except OSError:
        return None


def _tls_info() -> dict[str, Any]:
    """Inspect Caddy's local CA cert if present."""
    from pathlib import Path as _P  # noqa: PLC0415

    # Caddy local-CA cert lives under one of these paths depending on Caddy version.
    candidates = list(_P("/var/lib/caddy/.local/share/caddy/certificates").rglob("*.crt")) if _P(
        "/var/lib/caddy/.local/share/caddy/certificates"
    ).exists() else []
    fingerprint = "—"
    expires = "—"
    if candidates:
        try:
            import subprocess  # noqa: PLC0415

            cert = str(candidates[0])
            fp_out = subprocess.run(
                ["openssl", "x509", "-in", cert, "-noout", "-fingerprint", "-sha256"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if fp_out.returncode == 0 and "=" in fp_out.stdout:
                fingerprint = fp_out.stdout.split("=", 1)[1].strip()
            exp_out = subprocess.run(
                ["openssl", "x509", "-in", cert, "-noout", "-enddate"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if exp_out.returncode == 0 and "=" in exp_out.stdout:
                expires = exp_out.stdout.split("=", 1)[1].strip()
        except (FileNotFoundError, subprocess.SubprocessError):
            pass
    return {
        "type": "Caddy self-signed (internal CA)",
        "fingerprint": fingerprint,
        "expires": expires,
        "hsts": True,
    }


def _ssh_info() -> dict[str, Any]:
    """SSH state: enabled?, key count, last login."""
    import subprocess  # noqa: PLC0415
    from pathlib import Path as _P  # noqa: PLC0415

    enabled = False
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "ssh"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        enabled = r.stdout.strip() == "active"
    except (FileNotFoundError, subprocess.SubprocessError):
        # Try `sshd` unit too
        try:
            r = subprocess.run(
                ["systemctl", "is-active", "sshd"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            enabled = r.stdout.strip() == "active"
        except (FileNotFoundError, subprocess.SubprocessError):
            pass

    key_count = 0
    for u in ("pi01", "ubuntu", "pi"):
        ak = _P(f"/home/{u}/.ssh/authorized_keys")
        if ak.is_file():
            try:
                key_count += sum(
                    1
                    for ln in ak.read_text().splitlines()
                    if ln.strip() and not ln.startswith("#")
                )
            except OSError:
                pass

    last_login = "—"
    try:
        r = subprocess.run(
            ["last", "-n", "1", "-F"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if r.returncode == 0 and r.stdout.strip():
            last_login = r.stdout.splitlines()[0].strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

    return {
        "enabled": enabled,
        "port": 22,
        "key_count": key_count,
        "last_login": last_login,
    }


@router.get("/storage")
async def storage_info(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    """Real disk usage for the photos partition."""
    import shutil as _shutil  # noqa: PLC0415

    settings = get_settings()
    snap = snapshot()
    cap = used = 0
    fs = "—"
    try:
        usage = _shutil.disk_usage(str(settings.paths.photos))
        cap = usage.total
        used = usage.used
    except OSError:
        pass
    # Try to read the filesystem type from /proc/mounts.
    try:
        photos_path = str(settings.paths.photos)
        with open("/proc/mounts") as f:
            best_mp = ""
            for line in f:
                fields = line.split()
                if len(fields) >= 3:
                    mp = fields[1]
                    if photos_path.startswith(mp) and len(mp) > len(best_mp):
                        best_mp = mp
                        fs = fields[2]
    except OSError:
        pass
    return {
        "photos_root": str(settings.paths.photos),
        "thumb_root": str(settings.paths.thumbnails),
        "disk_used_pct": snap["disk_used_pct"],
        "capacity_bytes": cap,
        "used_bytes": used,
        "fs": fs,
    }


@router.get("/system")
async def system_info(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    """Real Pi 5 hardware identity + service / UPS / pairing state."""
    from arclap_station import __version__ as _arclap_version  # noqa: PLC0415

    snap = snapshot()
    station = get_station_store().load()
    return {
        "version": _arclap_version,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "snapshot": snap,
        "hw_model": _hw_model(),
        "hw_serial": _hw_serial() or station.serial,
        "uptime_seconds": snap.get("uptime_seconds", 0),
        "watchdog": _watchdog_state(),
        "ups": _ups_state(),
        "cloud": _cloud_state(station),
        "firmware": _firmware_state(_arclap_version),
    }


def _watchdog_state() -> dict[str, Any]:
    """Real watchdog status: hardware (kernel) + the cockpit's app watchdog."""
    import subprocess  # noqa: PLC0415
    from pathlib import Path as _P  # noqa: PLC0415

    kernel = _P("/dev/watchdog").exists()
    runtime_sec = 0
    try:
        r = subprocess.run(
            ["systemctl", "show", "-p", "RuntimeWatchdogUSec", "--value"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        # Returns e.g. "30s" or "0" — strip and parse.
        v = r.stdout.strip()
        if v.endswith("s"):
            runtime_sec = int(v[:-1])
        elif v.isdigit():
            runtime_sec = int(v)
    except (FileNotFoundError, subprocess.SubprocessError, ValueError):
        pass
    timer_active = False
    cam_timer_active = False
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "arclap-watchdog.timer"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        timer_active = r.stdout.strip() == "active"
        r2 = subprocess.run(
            ["systemctl", "is-active", "arclap-camera-watchdog.timer"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        cam_timer_active = r2.stdout.strip() == "active"
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return {
        "kernel_device": kernel,
        "kernel_runtime_sec": runtime_sec,
        "service_timer_active": timer_active,
        "camera_timer_active": cam_timer_active,
        "summary": (
            "active"
            if (kernel and runtime_sec > 0 and timer_active)
            else "partial"
            if (timer_active or kernel)
            else "inactive"
        ),
    }


def _ups_state() -> dict[str, Any]:
    """Best-effort UPS probe via apcaccess (apcupsd) or upsc (NUT)."""
    import subprocess  # noqa: PLC0415

    # apcupsd
    try:
        r = subprocess.run(
            ["apcaccess"], capture_output=True, text=True, timeout=3
        )
        if r.returncode == 0 and r.stdout:
            charge: float | None = None
            status_str = ""
            for line in r.stdout.splitlines():
                if line.startswith("BCHARGE"):
                    try:
                        charge = float(line.split(":", 1)[1].strip().split()[0])
                    except (ValueError, IndexError):
                        pass
                elif line.startswith("STATUS"):
                    status_str = line.split(":", 1)[1].strip()
            return {
                "detected": True,
                "driver": "apcupsd",
                "battery_pct": charge,
                "status": status_str or "unknown",
            }
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    # NUT
    try:
        r = subprocess.run(
            ["upsc", "ups@localhost"], capture_output=True, text=True, timeout=3
        )
        if r.returncode == 0 and r.stdout:
            return {
                "detected": True,
                "driver": "nut",
                "battery_pct": None,
                "status": "see upsc",
            }
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return {"detected": False, "driver": None, "battery_pct": None, "status": "not detected"}


def _cloud_state(station: Any) -> dict[str, Any]:
    """Cloud pairing state derived from station.json + settings."""
    s = get_settings()
    broker = getattr(s, "mqtt_broker_url", None) or getattr(s, "cloud_broker_url", None)
    cockpit = getattr(s, "cloud_base_url", None)
    return {
        "paired": bool(station.paired),
        "broker": broker if station.paired else None,
        "cockpit_url": cockpit if station.paired else None,
        "pair_token_set": bool(station.pair_token),
    }


def _firmware_state(version: str) -> dict[str, Any]:
    """Honest firmware metadata. No fake 'available: —'."""
    return {
        "current": version,
        "channel": "manual",
        "last_check": None,
        "available": None,
        "update_method": "sudo arclap-station-installer update",
    }


def _hw_model() -> str:
    """Read /sys/firmware/devicetree/base/model — e.g. 'Raspberry Pi 5 Model B Rev 1.0'."""
    from pathlib import Path as _P  # noqa: PLC0415

    p = _P("/sys/firmware/devicetree/base/model")
    if not p.is_file():
        return "Unknown"
    try:
        return p.read_text(errors="replace").rstrip("\x00").strip()
    except OSError:
        return "Unknown"


def _hw_serial() -> str:
    """Read CPU serial from /proc/cpuinfo (the 'Serial' line)."""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("Serial"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "—"


@router.get("/audit/recent")
async def audit_recent(
    limit: int = 100,
    _: dict[str, Any] = Depends(require_session),
) -> list[dict[str, Any]]:
    return recent_audit(limit=limit)


@router.get("/audit/export")
async def audit_export(
    start_id: int = 0,
    end_id: int | None = None,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """Signed export of the audit log for legal / forensic use.

    See arclap_station.audit.export_signed for the bundle schema and
    signing details (Ed25519 over the SHA-256 fingerprint of canonical
    JSON of entries). Falls back to fingerprint-only if no signing
    key is provisioned.
    """
    from arclap_station.audit import export_signed  # noqa: PLC0415

    bundle = export_signed(start_id=start_id, end_id=end_id)
    audit_emit("user", "audit.export", {
        "start_id": start_id,
        "end_id": end_id,
        "count": bundle["range"]["count"],
    })
    return bundle


@router.get("/logs/recent")
async def logs_recent(
    unit: str | None = None,
    level: str | None = None,
    q: str | None = None,
    limit: int = 200,
    _: dict[str, Any] = Depends(require_session),
) -> list[dict[str, Any]]:
    """Recent journald lines, normalised + newest-first.

    Returns the same `{ts, unit, level, message}` shape that the
    /logs-ws WebSocket emits so the cockpit can mix history and live
    streams into one sorted timeline. `unit`, `level` and `q` are
    server-side filters that match the cockpit's three controls.
    """
    return await recent_journal(unit=unit, level=level, query=q, limit=limit)


@router.websocket("/logs-ws")
async def logs_ws(
    ws: WebSocket,
    unit: str | None = None,
) -> None:
    """Stream journald lines to the cockpit Logs tab.

    Honours an optional `unit` query param so the operator's filter
    dropdown narrows what journalctl follows (cheaper + fewer events
    over the wire than client-side filtering of everything).
    """
    from arclap_station.api.deps import require_ws_session  # noqa: PLC0415

    sess = await require_ws_session(ws)
    if sess is None:
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        async for line in follow_journal(unit=unit):
            await ws.send_text(json.dumps(line))
    except WebSocketDisconnect:
        return
    except Exception:  # noqa: BLE001
        try:
            await ws.close(code=1011)
        except Exception:  # noqa: BLE001
            pass


@router.post("/pair")
async def pair_now(
    payload: dict[str, Any],
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """Exchange a pair-token for mTLS cert + broker URL via the Admin API.

    Falls back to local-only "registered" state if ARCLAP_CLOUD_API_URL
    isn't set (dev / standalone) — so the cockpit's pairing UX still
    works during testing without an admin backend.
    """
    code = str(payload.get("pair_code", "")).strip()
    force = bool(payload.get("force", False))
    if not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="pair_code required")
    from arclap_station.cloud.pairing import pair as cloud_pair  # noqa: PLC0415

    result = cloud_pair(code, force=force)
    if result.ok:
        # Successful AWS pairing — also kick the MQTT publisher.
        try:
            from arclap_station.cloud.mqtt import get_publisher  # noqa: PLC0415

            get_publisher().start()
        except Exception:  # noqa: BLE001
            pass
        return result.to_dict()
    # Cloud API unreachable → fall back to local-only state so the
    # cockpit doesn't permanently 4xx. We still emit a distinct audit
    # event so the operator knows pairing wasn't real.
    if result.error and "ARCLAP_CLOUD_API_URL" in result.error:
        cfg = get_station_store().update(pair_token=code, paired=True)
        audit_emit("user", "cloud.pair_local_only", {"paired": True})
        return {**cfg.to_dict(), "local_only": True, "warning": result.error}
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.error or "pair failed")


@router.post("/unpair")
async def unpair(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    """Reverse pairing — wipe certs + station.json flags."""
    from arclap_station.cloud.pairing import unpair as cloud_unpair  # noqa: PLC0415

    result = cloud_unpair()
    try:
        from arclap_station.cloud.mqtt import get_publisher  # noqa: PLC0415

        get_publisher().stop()
    except Exception:  # noqa: BLE001
        pass
    audit_emit("user", "cloud.unpair", {})
    return result.to_dict()


# ----- Danger Zone --------------------------------------------------------

class RestartRequest(BaseModel):
    unit: str = "arclap-station"


@router.post("/restart-service")
async def restart_service(
    payload: RestartRequest,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """Restart a systemd unit. Allowlisted to arclap-* + caddy for safety."""
    import subprocess  # noqa: PLC0415

    allowed = {
        "arclap-station",
        "arclap-station.service",
        "arclap-camera-watchdog",
        "arclap-camera-watchdog.timer",
        "arclap-retention",
        "arclap-retention.timer",
        "arclap-watchdog",
        "arclap-watchdog.timer",
        "caddy",
        "caddy.service",
        "avahi-daemon",
        "avahi-daemon.service",
    }
    if payload.unit not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unit '{payload.unit}' not in allowlist",
        )
    audit_emit("user", "system.restart_service", {"unit": payload.unit})
    # Fire-and-forget — the request that triggered the restart may itself
    # be the one we're killing. Spawn a detached subprocess.
    subprocess.Popen(
        ["systemctl", "restart", payload.unit],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"ok": True, "unit": payload.unit}


class RebootRequest(BaseModel):
    confirm_pin: str = Field(..., min_length=4, max_length=12, pattern=r"^\d+$")


@router.post("/reboot")
async def reboot(
    payload: RebootRequest,
    request: Request,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """Reboot the Pi. Requires PIN confirmation to avoid misclicks."""
    import subprocess  # noqa: PLC0415

    from arclap_station.auth import AuthManager, InvalidPin, LockedOut, PinNotSet  # noqa: PLC0415

    ip = request.client.host if request.client else "unknown"
    auth = AuthManager()
    try:
        auth.verify_pin(payload.confirm_pin, ip)
    except (LockedOut, PinNotSet) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidPin as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="PIN incorrect"
        ) from exc
    audit_emit("user", "system.reboot", {"ip": ip})
    subprocess.Popen(
        ["systemctl", "reboot"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"ok": True, "message": "reboot scheduled"}


class FactoryResetRequest(BaseModel):
    confirm_pin: str = Field(..., min_length=4, max_length=12, pattern=r"^\d+$")
    purge_photos: bool = False


@router.post("/factory-reset")
async def factory_reset(
    payload: FactoryResetRequest,
    request: Request,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """Wipe configuration and (optionally) photos. Requires PIN confirmation.

    What gets cleared:
    - /etc/arclap/auth.json (PIN)
    - /etc/arclap/station.json (station identity → resets first_boot)
    - destinations table
    - schedules table
    - audit_log table
    - upload_queue table

    What's preserved unless `purge_photos=True`:
    - /media/sdcard/photos/* (captured photos themselves)
    - photos DB rows
    """
    import shutil  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    from arclap_station.auth import AuthManager, InvalidPin, LockedOut, PinNotSet  # noqa: PLC0415

    ip = request.client.host if request.client else "unknown"
    auth = AuthManager()
    try:
        auth.verify_pin(payload.confirm_pin, ip)
    except (LockedOut, PinNotSet) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidPin as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="PIN incorrect"
        ) from exc

    audit_emit(
        "user",
        "system.factory_reset",
        {"ip": ip, "purge_photos": payload.purge_photos},
    )

    from arclap_station.config import get_settings as _gs  # noqa: PLC0415
    from arclap_station.db import get_db as _gdb  # noqa: PLC0415

    s = _gs()
    db = _gdb()
    # Wipe destination + schedule + audit + queue tables. Keep `photos`
    # intact unless purge_photos is set.
    with db.tx() as conn:
        conn.execute("DELETE FROM destinations")
        conn.execute("DELETE FROM schedules")
        conn.execute("DELETE FROM upload_queue")
        conn.execute("DELETE FROM audit_log")
        conn.execute("DELETE FROM tokens")
        if payload.purge_photos:
            conn.execute("DELETE FROM photos")
    # Delete on-disk secrets and identity.
    for p in [s.paths.etc / "auth.json", s.paths.etc / "station.json"]:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    # Optionally wipe captured photos.
    if payload.purge_photos and s.paths.photos.exists():
        try:
            shutil.rmtree(s.paths.photos, ignore_errors=True)
            s.paths.photos.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    # Schedule a service restart so the lifespan re-initializes a clean state.
    subprocess.Popen(
        ["systemctl", "restart", "arclap-station"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"ok": True, "message": "factory reset complete; service is restarting"}


__all__ = ["router", "StationConfig"]
