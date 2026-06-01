"""Watchdog sd_notify + boot-time DB integrity auto-restore."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from arclap_station import watchdog_notify as wd


# ── sd_notify watchdog ───────────────────────────────────────────────

def test_notify_noop_without_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """Off systemd (no NOTIFY_SOCKET) every notify is a safe no-op."""
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert wd.notify_ready() is False
    assert wd.notify_watchdog() is False


def test_watchdog_interval_none_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    assert wd.watchdog_interval_seconds() is None


def test_watchdog_interval_is_half(monkeypatch: pytest.MonkeyPatch) -> None:
    # systemd exports the full WatchdogSec in microseconds; we ping at half.
    monkeypatch.setenv("WATCHDOG_USEC", str(60_000_000))  # 60s
    assert wd.watchdog_interval_seconds() == 30.0


def test_watchdog_interval_rejects_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WATCHDOG_USEC", "not-a-number")
    assert wd.watchdog_interval_seconds() is None
    monkeypatch.setenv("WATCHDOG_USEC", "0")
    assert wd.watchdog_interval_seconds() is None


# ── boot-time DB integrity guard ─────────────────────────────────────

def test_integrity_guard_noop_on_healthy_db(fresh_db: Any) -> None:
    """A healthy DB → guard does nothing."""
    from arclap_station.backup import ensure_db_integrity_on_boot  # noqa: PLC0415

    res = ensure_db_integrity_on_boot()
    assert res["ok"] is True
    assert res["action"] == "none"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows keeps the SQLite mmap handle open so the corrupt file "
    "can't be replaced in-test; the restore path is Linux (the Pi) and is "
    "verified live there.",
)
def test_integrity_guard_restores_corrupt_db(fresh_db: Any) -> None:
    """A corrupt state.db is auto-restored from the latest snapshot."""
    from arclap_station.backup import ensure_db_integrity_on_boot, take_snapshot  # noqa: PLC0415
    from arclap_station.config import get_settings  # noqa: PLC0415
    from arclap_station.db import get_db, reset_db_singleton  # noqa: PLC0415

    # Put a known row in, then snapshot it.
    db = get_db()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO photos(path, captured_at, size_bytes) VALUES(?,?,?)",
            ("/tmp/x.jpg", "2026-05-21T00:00:00+00:00", 100),
        )
    snap = take_snapshot()
    assert snap["ok"], snap

    # Corrupt the live DB: close the handle, overwrite with garbage.
    reset_db_singleton()
    db_path = get_settings().paths.state_db
    # Remove WAL sidecars so the garbage main file is what's read.
    for suffix in ("-wal", "-shm"):
        p = db_path.parent / (db_path.name + suffix)
        if p.exists():
            p.unlink()
    db_path.write_bytes(b"this is not a sqlite database, it is garbage" * 50)

    # Guard should detect corruption and restore from the snapshot.
    res = ensure_db_integrity_on_boot()
    assert res["action"] == "restored"
    assert res["ok"] is True
    assert res["restored_from"].startswith("state-")

    # The corrupt file was preserved for forensics.
    corrupt = list(db_path.parent.glob(f"{db_path.name}.corrupt-*"))
    assert corrupt, "corrupt DB should be kept aside, not deleted"

    # Reopen — the restored DB is healthy and has our row.
    reset_db_singleton()
    db2 = get_db()
    with db2.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM photos WHERE path='/tmp/x.jpg'").fetchone()[0]
    assert int(n) == 1


def test_resilience_acceptance_checks_run(fresh_db: Any) -> None:
    """The new Resilience acceptance checks run without error and return
    valid states; DB-indexes + integrity + self-test pass on a fresh DB."""
    from arclap_station.acceptance import runner as r  # noqa: PLC0415

    results = {
        "DB indexes": r._check_db_indexes(),
        "DB integrity": r._check_db_integrity_acc(),
        "Self-test": r._check_self_test_green(),
        "Software watchdog": r._check_software_watchdog(),
        "SD noatime": r._check_noatime(),
        "Backup restorable": r._check_db_backup_restorable(),
        "UPS": r._check_ups(),
    }
    for name, res in results.items():
        assert res.state in ("ok", "fail", "skip"), f"{name}: bad state {res.state}"

    # On a fresh isolated test DB these are concretely true:
    assert results["DB indexes"].state == "ok", results["DB indexes"].detail
    assert results["DB integrity"].state == "ok", results["DB integrity"].detail
    # Self-test never 'bad' in the mock-camera test env.
    assert results["Self-test"].state in ("ok",), results["Self-test"].detail


def test_resilience_group_in_catalogue() -> None:
    """The Resilience group is wired into the acceptance catalogue."""
    from arclap_station.acceptance.runner import CHECKS  # noqa: PLC0415

    groups = {g for g, _, _ in CHECKS}
    assert "Resilience" in groups
    resilience = [name for g, name, _ in CHECKS if g == "Resilience"]
    assert "Software watchdog" in resilience
    assert "Backup restorable" in resilience


def test_integrity_guard_no_backup_reports_cleanly(fresh_db: Any) -> None:
    """Corrupt DB but no snapshot → guard reports failure, doesn't crash."""
    from arclap_station.backup import ensure_db_integrity_on_boot  # noqa: PLC0415
    from arclap_station.config import get_settings  # noqa: PLC0415
    from arclap_station.db import get_db, reset_db_singleton  # noqa: PLC0415

    get_db()  # ensure created
    reset_db_singleton()
    db_path = get_settings().paths.state_db
    for suffix in ("-wal", "-shm"):
        p = db_path.parent / (db_path.name + suffix)
        if p.exists():
            p.unlink()
    db_path.write_bytes(b"garbage" * 100)

    res = ensure_db_integrity_on_boot()
    # No backup to restore from → ok=False, but no exception.
    assert res["ok"] is False
