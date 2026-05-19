"""Operator PTY — pty.fork() into an interactive bash subshell.

v0.8.2 (was: ``bash --restricted`` which made the terminal useless —
no ``cd``, no ``/``-paths, no redirects, no env changes). Now we run
plain interactive bash. Safety comes from the layers below us:

  - systemd: service runs as the unprivileged ``arclap`` user, with
    ``NoNewPrivileges``, ``ProtectSystem=strict``, restricted device
    allowlist, and read-only ``/opt/arclap-station``. Anything the
    operator types is bounded by what that user is allowed to do.
  - ``sudo`` is aliased to ``/bin/false`` in the shell init — no
    privilege escalation from the cockpit.
  - rlimit on CPU + address space to bound runaway commands.
  - PATH is sane (``/opt/arclap/bin:/usr/local/bin:/usr/bin:/usr/sbin``)
    so the operator can actually run ``arclap-station``, ``gphoto2``,
    ``systemctl status``, ``journalctl``, ``ip``, ``ss``, etc.

Audit: every WS session logs ``terminal.session_start`` + ``…_end``
with the connecting IP, so a forensic chain exists. Commands typed are
NOT individually audited — that's the operator's keystroke log on
their workstation, not ours.

On non-POSIX platforms (Windows dev box) this raises ``PTYNotSupported``.
The frontend detects that and shows a "PTY unavailable in dev" banner.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import signal
from typing import Any

from arclap_station.config import get_settings

log = logging.getLogger(__name__)


class PTYNotSupported(RuntimeError):
    pass


class RestrictedPTY:
    """Owns a forked child + master fd. Methods are sync; wrap with asyncio."""

    def __init__(self, rows: int = 24, cols: int = 100) -> None:
        if platform.system() != "Linux":
            raise PTYNotSupported(
                f"restricted PTY is Linux-only; current platform={platform.system()}"
            )
        try:
            import pty  # noqa: PLC0415
        except ImportError as exc:  # noqa: BLE001
            raise PTYNotSupported(f"pty module unavailable: {exc}") from exc
        self._pty = pty
        self.rows = rows
        self.cols = cols
        self._pid: int | None = None
        self._fd: int | None = None
        self._closed = False

    def start(self) -> None:
        if self._pid is not None:
            return
        settings = get_settings()
        pid, fd = self._pty.fork()  # type: ignore[attr-defined]
        if pid == 0:  # child
            os.environ.clear()
            # Include /usr/sbin + /sbin so systemctl, ip, ss, etc. work
            # without absolute paths. /opt/arclap/bin holds arclap-station
            # + arclap-doctor symlinks.
            os.environ["PATH"] = (
                settings.pty_path
                if settings.pty_path
                else "/opt/arclap/bin:/usr/local/bin:/usr/bin:/usr/sbin:/bin:/sbin"
            )
            os.environ["HOME"] = "/var/lib/arclap"
            os.environ["TERM"] = "xterm-256color"
            os.environ["LANG"] = "C.UTF-8"
            os.environ["LC_ALL"] = "C.UTF-8"
            os.environ["ARCLAP_PTY"] = "1"
            # Color prompts so the operator sees something alive.
            os.environ["PS1"] = (
                r"\[\e[1;36m\]arclap\[\e[0m\]@\h:\[\e[1;33m\]\w\[\e[0m\]\$ "
            )
            try:
                os.chdir("/var/lib/arclap")
            except OSError:
                os.chdir("/tmp")
            shell = shutil.which("bash") or "/bin/bash"
            # An rcfile injected into bash --rcfile gives us colour ls +
            # convenience aliases without needing a global /etc/bash.bashrc
            # mutation. The heredoc is materialised to a temp file before
            # exec because bash needs --rcfile <path>.
            rc_path = "/tmp/arclap-pty.rc"
            try:
                with open(rc_path, "w") as fh:
                    fh.write(
                        "alias sudo=/bin/false\n"
                        "alias ls='ls --color=auto -lh'\n"
                        "alias ll='ls --color=auto -alh'\n"
                        "alias grep='grep --color=auto'\n"
                        "alias status='systemctl status arclap-station --no-pager'\n"
                        "alias logs='journalctl -u arclap-station -n 50 --no-pager'\n"
                        "alias tailog='journalctl -u arclap-station -f'\n"
                        "alias photos='ls /media/sdcard/photos/'\n"
                        "alias timers='systemctl list-timers --no-pager'\n"
                        "alias temp='vcgencmd measure_temp 2>/dev/null || cat /sys/class/thermal/thermal_zone0/temp'\n"
                        "alias usb='lsusb'\n"
                        "alias cam='gphoto2 --auto-detect'\n"
                        f"ulimit -t {settings.pty_cpu_seconds}\n"
                        f"ulimit -v {settings.pty_address_kb}\n"
                        "echo ''\n"
                        "echo -e '\\e[1;36marclap-station\\e[0m operator shell · sudo blocked · rlimited'\n"
                        "echo 'Try: \\033[33mstatus\\033[0m · \\033[33mlogs\\033[0m · \\033[33mtimers\\033[0m · "
                        "\\033[33mcam\\033[0m · \\033[33marclap-station support-bundle\\033[0m'\n"
                        "echo ''\n"
                    )
            except OSError:
                pass
            os.execvp(shell, [shell, "--rcfile", rc_path, "-i"])
            os._exit(127)
        else:
            self._pid = pid
            self._fd = fd

    async def read(self, max_bytes: int = 1024) -> bytes:
        if self._fd is None:
            raise RuntimeError("PTY not started")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _safe_read, self._fd, max_bytes)

    async def write(self, data: bytes) -> int:
        if self._fd is None:
            raise RuntimeError("PTY not started")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _safe_write, self._fd, data)

    def resize(self, rows: int, cols: int) -> None:
        if self._fd is None:
            return
        try:
            import fcntl  # noqa: PLC0415
            import struct  # noqa: PLC0415
            import termios  # noqa: PLC0415

            payload = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self._fd, termios.TIOCSWINSZ, payload)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._pid:
            try:
                os.kill(self._pid, signal.SIGHUP)  # type: ignore[attr-defined]
            except (ProcessLookupError, AttributeError):
                pass
            try:
                os.waitpid(self._pid, os.WNOHANG)  # type: ignore[attr-defined]
            except (ChildProcessError, AttributeError):
                pass
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass

    async def aclose(self) -> None:
        self.close()


def _safe_read(fd: int, n: int) -> bytes:
    try:
        return os.read(fd, n)
    except OSError:
        return b""


def _safe_write(fd: int, data: bytes) -> int:
    try:
        return os.write(fd, data)
    except OSError:
        return 0


def is_supported() -> bool:
    try:
        RestrictedPTY()
        return True
    except PTYNotSupported:
        return False


def info() -> dict[str, Any]:
    settings = get_settings()
    return {
        "supported": is_supported(),
        "path": settings.pty_path,
        "cpu_seconds": settings.pty_cpu_seconds,
        "memory_kb": settings.pty_address_kb,
        "platform": platform.system(),
    }
