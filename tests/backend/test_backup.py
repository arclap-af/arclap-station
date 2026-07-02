"""Backup snapshot atomicity + robust multi-candidate restore."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from arclap_station import backup
from arclap_station.config import get_settings
from arclap_station.db import get_db


def test_snapshot_valid_and_no_tmp_left(fresh_db: Any, tmp_path: Path) -> None:
    with get_db().tx() as conn:
        conn.execute(
            "INSERT INTO photos(path, captured_at, size_bytes) VALUES('/x.jpg','2026-05-21T00:00:00+00:00',1)"
        )
    res = backup.take_snapshot()
    assert res["ok"], res
    snap = backup.latest_snapshot()
    assert snap is not None and snap.exists()
    # Atomic write leaves no *.tmp behind.
    root = get_settings().paths.var / "backups"
    assert not list(root.glob("*.tmp")), "temp files must not survive a snapshot"
    # And the archive decompresses + integrity-checks clean.
    assert backup._decompress_and_verify(snap, tmp_path / "chk.db") is True


def test_decompress_and_verify_rejects_corrupt(fresh_db: Any, tmp_path: Path) -> None:
    bad = tmp_path / "bad.db.gz"
    bad.write_bytes(b"not a gzip file at all")
    assert backup._decompress_and_verify(bad, tmp_path / "out.db") is False


def test_restore_reports_no_valid_backup_when_all_corrupt(fresh_db: Any) -> None:
    root = get_settings().paths.var / "backups"
    root.mkdir(parents=True, exist_ok=True)
    # A truncated/corrupt "newest" snapshot must NOT be blindly restored.
    (root / "state-20260101-000000.db.gz").write_bytes(b"nope")
    res = backup.restore_latest()
    assert res["ok"] is False
    assert res["reason"] == "no_valid_backup", res
