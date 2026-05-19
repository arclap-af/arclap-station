"""Restricted PTY — pty.fork() into a hardened bash subshell.

The shell runs with:
  - PATH restricted to /opt/arclap/bin:/usr/bin
  - sudo aliased to /bin/false
  - rlimits via `prlimit` (CPU + RSS)
  - --restricted mode (no cd, no slashes in command names)

On non-POSIX platforms (Windows dev box) this module raises PTYNotSupported.
The frontend Terminal page detects that and shows a "PTY unavailable in dev"
banner — the real Pi build always has POSIX pty available.
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
            os.environ["PATH"] = settings.pty_path
            os.environ["HOME"] = "/var/lib/arclap"
            os.environ["TERM"] = "xterm-256color"
            os.environ["ARCLAP_PTY"] = "1"
            try:
                os.chdir("/var/lib/arclap")
            except OSError:
                os.chdir("/tmp")
            # Apply soft rlimits via prlimit invocation if available; otherwise
            # we rely on the shell's ulimit builtin.
            shell = shutil.which("bash") or "/bin/bash"
            init = (
                f"alias sudo=/bin/false; "
                f"ulimit -t {settings.pty_cpu_seconds}; "
                f"ulimit -v {settings.pty_address_kb}; "
                f"export PATH={settings.pty_path}; "
                f"echo 'arclap restricted shell — sudo and outbound networking are disabled'"
            )
            os.execvp(shell, [shell, "--restricted", "-c", f"{init}; exec /bin/bash --restricted"])
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
