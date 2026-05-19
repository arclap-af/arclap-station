"""Runtime configuration loader.

All filesystem paths are overridable via env vars so the same code runs on a
real Pi (default paths under /etc, /var/lib, /media) and on dev machines
(everything under a single workspace dir).
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _path_env(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


@dataclass(frozen=True)
class Paths:
    """Filesystem layout."""

    etc: Path
    var: Path
    photos: Path
    thumbnails: Path

    @property
    def auth_file(self) -> Path:
        return self.etc / "auth.json"

    @property
    def station_file(self) -> Path:
        return self.etc / "station.json"

    @property
    def destinations_dir(self) -> Path:
        return self.etc / "destinations"

    @property
    def state_db(self) -> Path:
        return self.var / "state.db"

    @property
    def scheduler_db(self) -> Path:
        return self.var / "scheduler.db"

    @property
    def session_secret_file(self) -> Path:
        return self.etc / "session.secret"

    def ensure(self) -> None:
        """Create directories if they don't already exist."""
        for p in (self.etc, self.var, self.photos, self.thumbnails, self.destinations_dir):
            p.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Settings:
    """Application-wide configuration."""

    paths: Paths
    bind_host: str = "127.0.0.1"
    bind_port: int = 8080
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    pin_lockout_max_attempts: int = 5
    pin_lockout_seconds: int = 900  # 15 minutes
    session_max_age_seconds: int = 60 * 60 * 12  # 12h
    # PATH inside the operator shell — wide enough for systemctl, ip,
    # ss, gphoto2, journalctl, lsusb, smartctl. Outside the operator
    # shell the service's own systemd hardening still applies.
    pty_path: str = "/opt/arclap/bin:/usr/local/bin:/usr/bin:/usr/sbin:/bin:/sbin"
    # 5 minutes of CPU per spawned process (was 30s — too aggressive,
    # `journalctl -f` got killed before the operator could read it).
    pty_cpu_seconds: int = 300
    pty_address_kb: int = 1_048_576  # 1 GB virtual address space
    cloud_base_url: str = "https://cloud.arclap.ch"
    skip_disk_pct_threshold: int = 90
    use_mock_camera: bool = False
    journal_unit: str = "arclap-station"
    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> Settings:
        default_root = os.environ.get("ARCLAP_DEV_ROOT")
        if default_root:
            root = Path(default_root).expanduser()
            paths = Paths(
                etc=_path_env("ARCLAP_ETC", str(root / "etc")),
                var=_path_env("ARCLAP_VAR", str(root / "var")),
                photos=_path_env("ARCLAP_PHOTOS", str(root / "photos")),
                thumbnails=_path_env("ARCLAP_THUMBS", str(root / "thumbnails")),
            )
        else:
            paths = Paths(
                etc=_path_env("ARCLAP_ETC", "/etc/arclap"),
                var=_path_env("ARCLAP_VAR", "/var/lib/arclap"),
                photos=_path_env("ARCLAP_PHOTOS", "/media/sdcard/photos"),
                thumbnails=_path_env("ARCLAP_THUMBS", "/var/lib/arclap/thumbnails"),
            )

        return cls(
            paths=paths,
            bind_host=os.environ.get("ARCLAP_BIND_HOST", "127.0.0.1"),
            bind_port=int(os.environ.get("ARCLAP_BIND_PORT", "8080")),
            use_mock_camera=os.environ.get("ARCLAP_MOCK_CAMERA", "0") in ("1", "true", "True"),
        )

    def session_secret(self) -> bytes:
        """Load or generate the session signing secret."""
        f = self.paths.session_secret_file
        if not f.exists():
            self.paths.ensure()
            f.write_bytes(secrets.token_bytes(64))
            try:
                os.chmod(f, 0o600)
            except OSError:  # noqa: S110 - Windows has no chmod
                pass
        return f.read_bytes()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton accessor."""
    return Settings.from_env()


def reset_settings_cache() -> None:
    """Test hook — clears the lru_cache so envvar overrides take effect."""
    get_settings.cache_clear()
