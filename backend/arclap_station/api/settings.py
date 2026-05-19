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
from arclap_station.telemetry.logs import follow_journal
from arclap_station.telemetry.metrics import snapshot
from arclap_station.terminal.pty import info as pty_info

router = APIRouter(prefix="/api/settings", tags=["settings"])


class GeneralUpdateRequest(BaseModel):
    name: str | None = None
    timezone: str | None = None
    lat: float | None = None
    lon: float | None = None


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
    return results


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


@router.websocket("/logs-ws")
async def logs_ws(ws: WebSocket) -> None:
    from arclap_station.api.deps import require_ws_session  # noqa: PLC0415

    sess = await require_ws_session(ws)
    if sess is None:
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        async for line in follow_journal():
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
    payload: dict[str, str],
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    code = payload.get("pair_code", "").strip()
    if not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="pair_code required")
    cfg = get_station_store().update(pair_token=code, paired=True)
    audit_emit("user", "cloud.pair", {"paired": True})
    return cfg.to_dict()


@router.post("/unpair")
async def unpair(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    cfg = get_station_store().update(pair_token=None, paired=False)
    audit_emit("user", "cloud.unpair", {})
    return cfg.to_dict()


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
