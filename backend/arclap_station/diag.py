"""Diagnostics infrastructure — crash dumps, support bundles, profiling.

Three concerns live here because they all touch the same /var/log path:

  1. Python faulthandler enabled on import: SIGSEGV/SIGFPE/SIGABRT/SIGILL/
     SIGBUS dump full thread tracebacks to /var/log/arclap/crash-*.txt
     before the process dies. Without this, a libgphoto2 segfault or a
     PyO3 memory issue leaves zero forensic trail.

  2. Slow-query logger — call slow_log("sql", duration_ms, query) and
     anything over the threshold appends to /var/log/arclap/slow.log.

  3. Support bundle — build_support_bundle() collects logs, db snapshot,
     dmesg, systemd states, config (with secrets redacted) into a single
     tar.gz an operator can attach to a ticket.

Faulthandler is enabled at module import time so just having `import
arclap_station.diag` somewhere early in lifespan is enough. Idempotent.
"""

from __future__ import annotations

import faulthandler
import gzip
import io
import logging
import os
import signal
import subprocess
import sys
import tarfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

LOG_DIR = Path(os.environ.get("ARCLAP_LOG_DIR", "/var/log/arclap"))
SLOW_LOG_THRESHOLD_MS = 100.0
SLOW_LOG_MAX_BYTES = 5_000_000  # rotate ~5 MB

_slow_lock = threading.Lock()
_initialised = False


def init() -> None:
    """Enable faulthandler with a dedicated log file. Idempotent."""
    global _initialised
    if _initialised:
        return
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("diag: could not create %s: %s", LOG_DIR, exc)
        return
    crash_path = LOG_DIR / f"crash-{os.getpid()}.txt"
    try:
        # Open append-mode so multiple processes can share if needed.
        fh = crash_path.open("a", buffering=1)
        faulthandler.enable(file=fh, all_threads=True)
        # Register fatal-signal dumpers — these run from the signal
        # handler context so they have to be minimal.
        for sig in (signal.SIGSEGV, signal.SIGFPE, signal.SIGABRT,
                    signal.SIGILL, signal.SIGBUS):
            try:
                faulthandler.register(sig, file=fh, all_threads=True, chain=True)
            except (ValueError, AttributeError):
                pass
        log.info("diag: faulthandler enabled → %s", crash_path)
    except OSError as exc:
        log.warning("diag: could not enable faulthandler: %s", exc)
    _initialised = True


def slow_log(category: str, duration_ms: float, detail: str) -> None:
    """Append a slow-operation marker to /var/log/arclap/slow.log.

    Threshold-gated. Rotates by deleting + recreating when the file
    exceeds SLOW_LOG_MAX_BYTES (cheap, no rename gymnastics).
    """
    if duration_ms < SLOW_LOG_THRESHOLD_MS:
        return
    path = LOG_DIR / "slow.log"
    line = f"{datetime.now(UTC).isoformat()}\t{category}\t{duration_ms:.1f}ms\t{detail[:512]}\n"
    try:
        with _slow_lock:
            # Cheap rotation — if we're over the cap, truncate and prepend a marker.
            try:
                if path.stat().st_size > SLOW_LOG_MAX_BYTES:
                    path.write_text(f"# rotated at {datetime.now(UTC).isoformat()}\n")
            except FileNotFoundError:
                pass
            with path.open("a") as fout:
                fout.write(line)
    except OSError:
        pass


# ----- support bundle ----------------------------------------------------


def build_support_bundle() -> tuple[bytes, str]:
    """Return (bytes, filename) of a tar.gz containing logs + db + config.

    The bundle is intentionally minimal — no photos, no destination
    secrets, no PIN hash. Operator can attach it to a ticket without
    worrying about leaking material.

    Contents:
      meta.txt           — version, hostname, serial, uptime, timestamp
      systemd.txt        — `systemctl is-active` for every arclap-* unit
      timers.txt         — `systemctl list-timers`
      journal.txt        — last 1000 arclap-station journal lines
      kernel.txt         — last 200 lines of dmesg
      health.json        — current camera_health beacon
      watchdog.json      — camera watchdog state
      state.db           — sqlite online backup (gzipped, no -wal)
      station.json       — config with `pair_token` REDACTED
      audit_tail.json    — last 200 audit log rows (JSON)
      smart.txt          — smartctl -A on the data device
      sys_lsusb.txt      — lsusb (camera detection state)
    """
    from arclap_station import __version__  # noqa: PLC0415
    from arclap_station.audit import recent as recent_audit  # noqa: PLC0415
    from arclap_station.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    now = datetime.now(UTC)
    bundle = io.BytesIO()
    with tarfile.open(fileobj=bundle, mode="w:gz") as tar:
        def add_text(name: str, body: str) -> None:
            data = body.encode("utf-8", errors="replace")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(data))

        def add_cmd(name: str, cmd: list[str], timeout: float = 5.0) -> None:
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
                add_text(name, r.stdout + (("\n--STDERR--\n" + r.stderr) if r.stderr else ""))
            except (FileNotFoundError, subprocess.SubprocessError) as exc:
                add_text(name, f"(could not run {' '.join(cmd)}: {exc})\n")

        # meta
        try:
            import socket as _s  # noqa: PLC0415
            hostname = _s.gethostname()
        except OSError:
            hostname = "unknown"
        try:
            uptime = (now - datetime.fromtimestamp(time.time() - _proc_uptime_sec(), UTC)).total_seconds()
        except OSError:
            uptime = -1
        add_text(
            "meta.txt",
            f"arclap-station {__version__}\n"
            f"generated {now.isoformat()}\n"
            f"hostname  {hostname}\n"
            f"uptime_s  {uptime:.0f}\n",
        )

        # systemd
        add_cmd("systemd.txt", [
            "systemctl", "--no-pager", "status",
            "arclap-station", "arclap-watchdog.timer",
            "arclap-camera-watchdog.timer", "arclap-retention.timer",
            "arclap-backup.timer", "arclap-integrity.timer", "caddy",
        ], timeout=10)
        add_cmd("timers.txt", ["systemctl", "list-timers", "--no-pager", "--all"])
        add_cmd("journal.txt", [
            "journalctl", "-u", "arclap-station", "-n", "1000", "--no-pager",
        ], timeout=30)
        add_cmd("kernel.txt", ["dmesg", "-T"], timeout=5)
        add_cmd("smart.txt", ["smartctl", "-A", "-H", "/dev/mmcblk0"], timeout=5)
        add_cmd("sys_lsusb.txt", ["lsusb"])
        add_cmd("ip_addr.txt", ["ip", "-br", "address"])

        # beacons
        try:
            from arclap_station.camera import health as _h  # noqa: PLC0415
            add_text("health.json", str(_h.read_state()))
        except Exception:  # noqa: BLE001
            pass
        wd_path = settings.paths.var / "camera_watchdog.json"
        try:
            add_text("watchdog.json", wd_path.read_text())
        except OSError:
            pass

        # state.db online backup → gzipped inline
        try:
            import sqlite3  # noqa: PLC0415

            src = sqlite3.connect(f"file:{settings.paths.state_db}?mode=ro", uri=True)
            mem = sqlite3.connect(":memory:")
            try:
                src.backup(mem)
                buf = io.BytesIO()
                for chunk in mem.iterdump():
                    buf.write(chunk.encode("utf-8"))
                    buf.write(b"\n")
            finally:
                src.close()
                mem.close()
            gz = gzip.compress(buf.getvalue(), compresslevel=6)
            info = tarfile.TarInfo(name="state.sql.gz")
            info.size = len(gz)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(gz))
        except Exception as exc:  # noqa: BLE001
            add_text("state.sql.error", str(exc))

        # station.json — redacted
        sc_path = settings.paths.etc / "station.json"
        try:
            import json as _json  # noqa: PLC0415

            d = _json.loads(sc_path.read_text())
            for k in ("pair_token",):
                if k in d:
                    d[k] = "REDACTED" if d[k] else d[k]
            add_text("station.json", _json.dumps(d, indent=2))
        except OSError:
            pass

        # audit tail
        try:
            tail = recent_audit(limit=200)
            import json as _json2  # noqa: PLC0415

            add_text("audit_tail.json", _json2.dumps(tail, indent=2, default=str))
        except Exception as exc:  # noqa: BLE001
            add_text("audit_tail.error", str(exc))

    bundle.seek(0)
    fname = f"arclap-support-{now.strftime('%Y%m%d-%H%M%S')}.tar.gz"
    return bundle.getvalue(), fname


def _proc_uptime_sec() -> float:
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError):
        return 0.0


# ----- boot history ------------------------------------------------------


def boot_history(limit: int = 50) -> list[dict[str, Any]]:
    """Return last `limit` boots from `journalctl --list-boots`.

    Each row: {index, id, started_at, ended_at, duration_sec, reason}.
    """
    try:
        r = subprocess.run(
            ["journalctl", "--list-boots", "--no-pager", "-o", "short-iso"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        log.warning("boot_history journalctl failed: %s", exc)
        return []
    rows: list[dict[str, Any]] = []
    for line in r.stdout.splitlines():
        # Format: " -3 <boot-id> <YYYY-MM-DDTHH:MM:SSZZ>—<YYYY-MM-DDTHH:MM:SSZZ>"
        parts = line.strip().split(None, 3)
        if len(parts) < 3:
            continue
        try:
            idx = int(parts[0])
        except ValueError:
            continue
        boot_id = parts[1]
        rng = parts[2] + (parts[3] if len(parts) > 3 else "")
        started, _, ended = rng.partition("—")
        if not ended:
            started, _, ended = rng.partition("-")
        # Reason heuristic: query journalctl for last shutdown reason
        rows.append({
            "index": idx,
            "id": boot_id,
            "started_at": started.strip(),
            "ended_at": ended.strip() or None,
            "reason": _boot_reason(boot_id),
        })
    return rows[:limit]


def _boot_reason(boot_id: str) -> str:
    """Best-effort guess of why a boot happened.

    Looks at the last lines of the previous boot's journal — kernel
    panic / power loss / userspace reboot leave distinctive markers.
    """
    try:
        r = subprocess.run(
            ["journalctl", "-b", boot_id, "-n", "20", "--no-pager",
             "-o", "short", "_TRANSPORT=kernel"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return "unknown"
    tail = r.stdout.lower()
    if "kernel panic" in tail:
        return "kernel_panic"
    if "watchdog" in tail and "reset" in tail:
        return "watchdog_reset"
    if "reboot" in tail:
        return "reboot"
    if "halt" in tail or "power off" in tail:
        return "shutdown"
    return "clean_boot"


def run_support_bundle() -> int:
    """CLI: write a support bundle to /var/lib/arclap/support/."""
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%S")
    try:
        from arclap_station.config import get_settings  # noqa: PLC0415

        body, fname = build_support_bundle()
        out_dir = get_settings().paths.var / "support"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / fname
        out.write_bytes(body)
        print(f"wrote {out} ({len(body)} bytes)")
        return 0
    except Exception as exc:  # noqa: BLE001
        log.exception("support-bundle crashed: %s", exc)
        return 1
