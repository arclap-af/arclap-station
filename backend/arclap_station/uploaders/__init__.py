"""Uploader plug-in interface.

Each destination type implements `Uploader` and is registered in `REGISTRY`.
The retry queue (`queue.py`) drives them with exponential back-off.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class UploadError(Exception):
    """Raised by an Uploader to flag a recoverable failure."""


@runtime_checkable
class Uploader(Protocol):
    """The contract every destination backend honours."""

    type: str
    name: str

    def test(self) -> dict[str, Any]:
        """Round-trip probe: write -> list -> read -> compare -> delete."""

    def upload(self, local: Path, key: str) -> dict[str, Any]:
        """Upload a single file under the destination's namespace."""

    def delete_remote(self, key: str) -> bool:
        """Remove a previously uploaded object. Best-effort."""

    def close(self) -> None: ...


UploaderFactory = Callable[[str, str, dict[str, Any]], Uploader]


REGISTRY: dict[str, UploaderFactory] = {}


def register(type_id: str) -> Callable[[UploaderFactory], UploaderFactory]:
    def decorator(fn: UploaderFactory) -> UploaderFactory:
        REGISTRY[type_id] = fn
        return fn

    return decorator


def build(uploader_id: str, name: str, type_id: str, config: dict[str, Any]) -> Uploader:
    if type_id not in REGISTRY:
        raise ValueError(f"unknown destination type: {type_id}")
    return REGISTRY[type_id](uploader_id, name, config)


# Import sub-modules so their @register decorators run.
from arclap_station.uploaders import ftp as _ftp  # noqa: E402, F401
from arclap_station.uploaders import local as _local  # noqa: E402, F401
from arclap_station.uploaders import mqtt as _mqtt  # noqa: E402, F401
from arclap_station.uploaders import s3 as _s3  # noqa: E402, F401
from arclap_station.uploaders import sftp as _sftp  # noqa: E402, F401
from arclap_station.uploaders import webhook as _webhook  # noqa: E402, F401

__all__ = ["Uploader", "UploadError", "REGISTRY", "register", "build"]
