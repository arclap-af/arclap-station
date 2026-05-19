"""Station identity file at /etc/arclap/station.json."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from arclap_station.config import Settings, get_settings


@dataclass
class StationConfig:
    name: str = "arclap-station"
    timezone: str = "UTC"
    hostname: str = ""
    serial: str = ""
    lat: float | None = None
    lon: float | None = None
    pair_token: str | None = None
    paired: bool = False
    first_boot_completed: bool = False
    # v0.8: new fields
    site: str = ""                       # short label, burned into watermark
    watermark: bool = False              # burn serial+site+ts on every JPEG
    project_starts_at: str | None = None # ISO8601, informational
    project_ends_at: str | None = None   # ISO8601, photos auto-purge N days after
    bandwidth_kbps: int | None = None    # per-station upload rate cap (None = unlimited)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StationConfigStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def _path(self) -> Path:
        return self._settings.paths.station_file

    def load(self) -> StationConfig:
        if not self._path.exists():
            return StationConfig()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return StationConfig(**{k: v for k, v in data.items() if k in StationConfig().__annotations__})
        except (json.JSONDecodeError, OSError, TypeError):
            return StationConfig()

    def save(self, config: StationConfig) -> None:
        self._settings.paths.ensure()
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(config.to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
        try:
            os.chmod(self._path, 0o644)
        except OSError:
            pass

    def update(self, **fields: Any) -> StationConfig:
        cfg = self.load()
        for k, v in fields.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        self.save(cfg)
        return cfg


_store: StationConfigStore | None = None


def get_station_store() -> StationConfigStore:
    global _store
    if _store is None:
        _store = StationConfigStore()
    return _store


def reset_station_store() -> None:
    global _store
    _store = None


def ensure_serial_from_cpu() -> None:
    """If station.serial is empty, read the Pi's CPU serial and persist it.

    Called on backend startup. Idempotent: existing serials are left
    alone so the field acts as a one-time write."""
    store = get_station_store()
    cfg = store.load()
    if cfg.serial:
        return
    serial = ""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("Serial"):
                    serial = line.split(":", 1)[1].strip()
                    break
    except OSError:
        return
    if serial:
        store.update(serial=serial)
