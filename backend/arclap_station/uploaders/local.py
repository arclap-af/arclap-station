"""Local-filesystem uploader (e.g. a mounted NAS at /mnt/nas)."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from arclap_station.uploaders import UploadError, register


class LocalUploader:
    type = "local"

    def __init__(self, uploader_id: str, name: str, config: dict[str, Any]) -> None:
        self.id = uploader_id
        self.name = name
        root = config.get("path") or config.get("root")
        if not root:
            raise ValueError("local uploader requires 'path'")
        self.root = Path(root).expanduser()
        self.retention_days = int(config.get("retention_days", 0))

    def _ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def test(self) -> dict[str, Any]:
        self._ensure()
        probe = self.root / f"arclap-probe-{int(time.time())}.txt"
        try:
            probe.write_bytes(b"x")
            content = probe.read_bytes()
            if content != b"x":
                raise UploadError("local probe content mismatch")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            raise UploadError(f"local probe failed: {exc}") from exc
        return {"ok": True, "root": str(self.root)}

    def upload(self, local: Path, key: str) -> dict[str, Any]:
        self._ensure()
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local, target)
        size = target.stat().st_size
        self._sweep()
        return {"ok": True, "remote_path": str(target), "bytes": size}

    def delete_remote(self, key: str) -> bool:
        target = self.root / key
        try:
            target.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def _sweep(self) -> None:
        if self.retention_days <= 0 or not self.root.exists():
            return
        cutoff = time.time() - self.retention_days * 86400
        for p in self.root.rglob("*"):
            if p.is_file():
                try:
                    if p.stat().st_mtime < cutoff:
                        p.unlink()
                except OSError:
                    continue

    def close(self) -> None:
        return None


@register("local")
def _build(uploader_id: str, name: str, config: dict[str, Any]) -> LocalUploader:
    return LocalUploader(uploader_id, name, config)
