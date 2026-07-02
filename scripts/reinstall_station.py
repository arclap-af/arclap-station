#!/usr/bin/env python3
"""Fully reinstall the Arclap Station on a fresh (or existing) Pi.

Run this from your laptop / desktop via a Windows or Unix command prompt:

    python scripts/reinstall_station.py --host arclap-pi
    python scripts/reinstall_station.py --host 192.168.10.28 --user pi01
    python scripts/reinstall_station.py --host arclap-pi --purge-photos    # nuclear

What it does, in order:

    1. SSH connectivity check (needs key-based auth; password prompts will hang)
    2. Stop arclap-station + caddy services if running
    3. Disable + remove old arclap-* systemd units
    4. Replace installed code (/opt/arclap-station, /var/www/arclap).
       STATE IS PRESERVED by default — the PIN, destination secrets, the
       DB and the audit log (/etc/arclap + /var/lib/arclap) survive a
       reinstall. Use --purge-state for a true factory reset; --purge-photos
       also deletes /media/sdcard/photos.
    5. apt update + install git (skipped if --no-apt)
    6. git clone the repo into /tmp/arclap-station (or pull --depth 1)
    7. sudo bash install.sh
    8. Wait for /api/health to return 200
    9. Print the cockpit URL and how to set the initial PIN

Requirements on your machine:
    - Python 3.8+
    - `ssh` and `scp` on PATH (OpenSSH on modern Windows works)
    - SSH key-based access to the Pi as a sudoer (passwordless sudo recommended)

Requirements on the Pi:
    - Ubuntu Server 26.04 arm64 (or Raspberry Pi OS Bookworm 64-bit)
    - Network reachable from your machine
    - Internet access (apt + GitHub clone)

Exit codes:
    0 = success
    1 = SSH connectivity failed
    2 = a remote step failed
    3 = service didn't come up healthy within the timeout
    4 = user aborted at the confirmation prompt
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_REPO = "https://github.com/arclap-af/arclap-station.git"
DEFAULT_BRANCH = "main"
HEALTH_TIMEOUT_SEC = 180

# Installed CODE / assets — always replaced on a reinstall (they're just
# the software; install.sh recreates them).
CODE_PATHS = [
    "/opt/arclap-station",
    "/var/www/arclap",
]
# STATE — the PIN (auth.json), destination secrets (dest.key), the SQLite
# DB and the hash-chained audit log. PRESERVED by default so a software
# reinstall doesn't lock the operator out or lose destination credentials;
# wiped only with --purge-state (a true factory reset). Photos + logs have
# their own flags.
STATE_DIRS = [
    "/etc/arclap",
    "/var/lib/arclap",
]

# Systemd units that may be installed from a previous run.
LEGACY_UNITS = [
    "arclap-station.service",
    "arclap-watchdog.service",
    "arclap-watchdog.timer",
    "arclap-camera-watchdog.service",
    "arclap-camera-watchdog.timer",
    "arclap-retention.service",
    "arclap-retention.timer",
    "arclap-backup.service",
    "arclap-backup.timer",
    "arclap-integrity.service",
    "arclap-integrity.timer",
    "arclap-timelapse.service",
    "arclap-timelapse.timer",
    "arclap-usb3-disable.service",
    "arclap-uploader.service",
]

# Polkit + drop-in files that the installer would overwrite but should be
# cleared first so wrong-content versions don't survive.
LEGACY_FILES = [
    "/etc/polkit-1/rules.d/50-arclap.rules",
    "/etc/udev/rules.d/50-arclap-camera.rules",
    "/etc/systemd/journald.conf.d/50-arclap.conf",
    "/etc/systemd/timesyncd.conf.d/50-arclap.conf",
    "/etc/systemd/timesyncd.conf.d/60-cockpit.conf",
    "/etc/systemd/resolved.conf.d/50-arclap.conf",
    "/etc/systemd/resolved.conf.d/60-cockpit.conf",
    "/etc/NetworkManager/conf.d/50-arclap.conf",
    "/etc/caddy/Caddyfile",
]

# ---------------------------------------------------------------------------
# Pretty output
# ---------------------------------------------------------------------------

# Force-flush so progress shows immediately when piped.
def echo(s: str, *, end: str = "\n") -> None:
    print(s, end=end, flush=True)

def step(n: int, total: int, label: str) -> None:
    bar = f"[{n:>2}/{total}]"
    echo(f"\n\033[1;36m{bar}\033[0m \033[1m{label}\033[0m")

def ok(msg: str) -> None:
    echo(f"  \033[32m✓\033[0m {msg}")

def warn(msg: str) -> None:
    echo(f"  \033[33m⚠\033[0m {msg}")

def fail(msg: str) -> None:
    echo(f"  \033[31m✗\033[0m {msg}")

# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------

@dataclass
class Remote:
    """Encapsulates the SSH target so command-running stays terse."""
    host: str
    user: str | None = None
    port: int = 22

    @property
    def addr(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host

    def run(self, cmd: str, *, check: bool = True, capture: bool = False, sudo: bool = False) -> subprocess.CompletedProcess[str]:
        """Run `cmd` on the remote. `sudo=True` prefixes with sudo -n (no
        password) so we fail loudly instead of hanging if sudo would prompt."""
        full = f"sudo -n bash -c {shlex.quote(cmd)}" if sudo else cmd
        ssh = ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes",
               "-p", str(self.port), self.addr, full]
        return subprocess.run(
            ssh,
            check=check,
            text=True,
            capture_output=capture,
        )

# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------

def check_ssh(remote: Remote) -> None:
    """Step 1 — fail fast if we can't reach the Pi with a passwordless key."""
    try:
        r = remote.run("uname -srm && hostname", capture=True)
    except subprocess.CalledProcessError as exc:
        fail(f"SSH to {remote.addr} failed: exit {exc.returncode}")
        echo("    Hint: copy your key with `ssh-copy-id <user>@<host>` and")
        echo("    make sure sudo is passwordless for that user (visudo: NOPASSWD).")
        sys.exit(1)
    info = (r.stdout or "").strip().replace("\n", " · ")
    ok(f"{remote.addr} reachable — {info}")
    # Sudo non-interactive probe.
    try:
        remote.run("true", sudo=True)
        ok("passwordless sudo confirmed")
    except subprocess.CalledProcessError:
        fail("passwordless sudo NOT working — add `<user> ALL=(ALL) NOPASSWD:ALL`")
        sys.exit(1)

def stop_services(remote: Remote) -> None:
    """Step 2 — stop the running arclap services so we can delete files."""
    # Best-effort: ignore failures (they may not be installed yet).
    units = " ".join(LEGACY_UNITS) + " caddy.service"
    remote.run(f"systemctl stop {units} || true", sudo=True, check=False)
    remote.run(f"systemctl disable {units} || true", sudo=True, check=False)
    ok("services stopped + disabled")

def remove_units(remote: Remote) -> None:
    """Step 3 — delete the old unit files so daemon-reload sees a clean state."""
    paths = " ".join(f"/etc/systemd/system/{u}" for u in LEGACY_UNITS) + " /etc/systemd/system/arclap-station.socket"
    remote.run(f"rm -f {paths}", sudo=True, check=False)
    remote.run("systemctl daemon-reload", sudo=True, check=False)
    ok(f"{len(LEGACY_UNITS)} legacy unit files removed")

def wipe_state(remote: Remote, *, purge_state: bool, purge_photos: bool, purge_logs: bool) -> None:
    """Step 4 — replace installed code; PRESERVE state (PIN/secrets/DB/audit)
    unless --purge-state. Photos + logs kept unless their own flag is set."""
    paths = list(CODE_PATHS)
    if purge_state:
        paths += STATE_DIRS
    if purge_photos:
        paths.append("/media/sdcard/photos")
    if purge_logs:
        paths.append("/var/log/arclap")
    for p in paths:
        remote.run(f"rm -rf {shlex.quote(p)}", sudo=True, check=False)
    # Regenerated-by-installer config drop-ins (caddy/udev/polkit/…) — safe
    # to clear so stale-content versions don't survive.
    for f in LEGACY_FILES:
        remote.run(f"rm -f {shlex.quote(f)}", sudo=True, check=False)
    if purge_state:
        # arclap's home is /var/lib/arclap; only drop the user when we're
        # actually purging state (install.sh recreates it).
        remote.run("userdel -r arclap 2>/dev/null || true; groupdel arclap 2>/dev/null || true",
                   sudo=True, check=False)
    ok(f"replaced {len(CODE_PATHS)} code dirs + {len(LEGACY_FILES)} config files")
    ok("STATE (PIN + secrets + DB + audit): "
       + ("WIPED — factory reset (--purge-state)" if purge_state else "PRESERVED"))
    if not purge_photos:
        ok("/media/sdcard/photos kept (use --purge-photos to nuke)")

def apt_install(remote: Remote, *, skip: bool) -> None:
    """Step 5 — refresh apt and ensure git + curl are present."""
    if skip:
        warn("apt step skipped (--no-apt)")
        return
    remote.run("apt-get update -qq", sudo=True)
    remote.run("apt-get install -y --no-install-recommends git curl ca-certificates",
               sudo=True)
    ok("apt updated + git/curl installed")

def clone_repo(remote: Remote, repo: str, branch: str) -> str:
    """Step 6 — shallow-clone the install scripts. Returns the path on the Pi."""
    target = "/tmp/arclap-station-fresh"
    remote.run(f"rm -rf {target}", check=False)
    remote.run(
        f"git clone --depth 1 --branch {shlex.quote(branch)} {shlex.quote(repo)} {target}"
    )
    sha = remote.run(f"git -C {target} rev-parse --short HEAD", capture=True).stdout.strip()
    ok(f"cloned {repo}@{branch} ({sha}) → {target}")
    return target

def run_installer(remote: Remote, path: str) -> None:
    """Step 7 — kick install.sh. Streams output so the operator can see progress."""
    echo("    streaming installer output below ──────────────────────────────")
    rc = remote.run(f"cd {path} && bash install.sh", sudo=True, check=False).returncode
    echo("    ────────────────────────────────────────────────────────────────")
    if rc != 0:
        fail(f"install.sh exited {rc}")
        sys.exit(2)
    ok("install.sh completed")

def wait_for_health(remote: Remote, timeout: int) -> None:
    """Step 8 — poll /api/health every 2s until it returns ok=true or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = remote.run(
                "curl -fsS --max-time 3 http://127.0.0.1:8080/api/health",
                capture=True, check=False,
            )
            if r.returncode == 0 and '"ok": true' in r.stdout.replace(" ", ""):
                ok(f"backend reports ok=true ({r.stdout.strip()[:120]}…)")
                return
        except subprocess.CalledProcessError:
            pass
        echo("    waiting for /api/health … ", end="\r")
        time.sleep(2)
    fail(f"service didn't reach healthy state within {timeout}s")
    sys.exit(3)

def print_final(remote: Remote) -> None:
    """Step 9 — show the operator how to log in."""
    try:
        ip = remote.run("hostname -I | awk '{print $1}'", capture=True).stdout.strip()
        hostname = remote.run("hostname", capture=True).stdout.strip()
    except subprocess.CalledProcessError:
        ip = "<unknown>"
        hostname = "<unknown>"
    echo("")
    echo("\033[1;32m" + "═" * 64 + "\033[0m")
    echo("\033[1;32m  Arclap Station is installed and running.\033[0m")
    echo("\033[1;32m" + "═" * 64 + "\033[0m")
    echo("")
    echo(f"  Cockpit URL (LAN):     \033[1;36mhttps://{hostname}.local/\033[0m")
    echo(f"  Cockpit URL (direct):  \033[1;36mhttps://{ip}/\033[0m")
    echo("")
    echo("  First visit:")
    echo("    1. Accept the self-signed certificate warning.")
    echo("    2. Walk the 10-step Setup wizard (PIN → station → camera → …).")
    echo("    3. Plug the DSLR; the Camera page should show 'connected'.")
    echo("")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="Fully reinstall the Arclap Station on a remote Pi.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--host", required=True,
                   help="Pi host or IP (e.g. arclap-pi, 192.168.10.28). Must be reachable via SSH with key auth.")
    p.add_argument("--user", default=None,
                   help="SSH user (omit to use ~/.ssh/config). Needs passwordless sudo on the Pi.")
    p.add_argument("--port", type=int, default=22, help="SSH port (default 22)")
    p.add_argument("--repo", default=DEFAULT_REPO,
                   help=f"Git URL to clone (default {DEFAULT_REPO})")
    p.add_argument("--branch", default=DEFAULT_BRANCH,
                   help=f"Branch or tag to install (default {DEFAULT_BRANCH})")
    p.add_argument("--purge-photos", action="store_true",
                   help="ALSO delete /media/sdcard/photos. DESTRUCTIVE — defaults off.")
    p.add_argument("--purge-state", action="store_true",
                   help="ALSO wipe /etc/arclap + /var/lib/arclap (PIN, secrets, DB, audit) — "
                        "a factory reset; you'll re-run the Setup wizard. Defaults off (state PRESERVED).")
    p.add_argument("--purge-logs", action="store_true",
                   help="Wipe /var/log/arclap too. Default keeps logs for forensics.")
    p.add_argument("--no-apt", action="store_true",
                   help="Skip the apt update + git install step (saves ~30s if you know it's done).")
    p.add_argument("--health-timeout", type=int, default=HEALTH_TIMEOUT_SEC,
                   help=f"Seconds to wait for /api/health (default {HEALTH_TIMEOUT_SEC}).")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Skip the confirmation prompt (use in CI).")
    args = p.parse_args()

    remote = Remote(host=args.host, user=args.user, port=args.port)

    echo("\033[1mArclap Station — fresh reinstall\033[0m")
    echo(f"  target:        {remote.addr}:{remote.port}")
    echo(f"  repo:          {args.repo} @ {args.branch}")
    echo(f"  purge photos:  {'YES — irreversible' if args.purge_photos else 'no (kept)'}")
    echo(f"  purge state:   {'YES — PIN/secrets/DB/audit erased' if args.purge_state else 'no (PRESERVED)'}")
    echo(f"  purge logs:    {'yes' if args.purge_logs else 'no (kept)'}")
    echo(f"  apt update:    {'skipped' if args.no_apt else 'yes'}")
    if not args.yes:
        ans = input("\nProceed? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            echo("Aborted.")
            return 4

    total = 9
    step(1, total, "SSH connectivity check")
    check_ssh(remote)

    step(2, total, "Stopping existing services")
    stop_services(remote)

    step(3, total, "Removing legacy systemd units")
    remove_units(remote)

    step(4, total, "Wiping install state")
    wipe_state(remote, purge_state=args.purge_state, purge_photos=args.purge_photos, purge_logs=args.purge_logs)

    step(5, total, "apt update + install git + curl")
    apt_install(remote, skip=args.no_apt)

    step(6, total, "Cloning the repo")
    src_path = clone_repo(remote, args.repo, args.branch)

    step(7, total, "Running install.sh")
    run_installer(remote, src_path)

    step(8, total, "Waiting for backend to report healthy")
    wait_for_health(remote, args.health_timeout)

    step(9, total, "Done")
    print_final(remote)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        echo("\nInterrupted.")
        sys.exit(130)
