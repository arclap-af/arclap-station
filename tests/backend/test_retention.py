"""Retention policy — the only code that deletes customer photos.

Regression guard for the P1 where emergency mode (disk >= 95%) deleted
EVERY non-starred photo, including un-uploaded originals, because the
early-exit was gated behind `if not emergency` and `_should_keep`
ignored upload state in emergency.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from arclap_station.retention import policy


# ── pure keep/delete logic ───────────────────────────────────────────

def test_unuploaded_is_never_deleted_any_tier_any_mode() -> None:
    for tier in ("hot", "warm", "cold", "archive"):
        for emergency in (False, True):
            assert policy._should_keep(tier, uploaded=False, starred=False, emergency=emergency) is True, \
                f"un-uploaded {tier} emergency={emergency} must be protected"


def test_starred_always_survives() -> None:
    assert policy._should_keep("archive", uploaded=True, starred=True, emergency=True) is True


def test_emergency_deletes_uploaded_hot_and_warm() -> None:
    assert policy._should_keep("hot", uploaded=True, starred=False, emergency=True) is False
    assert policy._should_keep("warm", uploaded=True, starred=False, emergency=True) is False


def test_normal_mode_tiers() -> None:
    assert policy._should_keep("hot", True, False, False) is True
    assert policy._should_keep("warm", True, False, False) is True
    assert policy._should_keep("cold", True, False, False) is False      # uploaded cold → deletable
    assert policy._should_keep("archive", True, False, False) is False


# ── full sweep in emergency ──────────────────────────────────────────

def test_emergency_sweep_protects_unuploaded_and_starred(
    fresh_db: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    from arclap_station.photos.store import get_store  # noqa: PLC0415

    store = get_store()
    old = datetime.now(UTC) - timedelta(days=120)  # archive age

    up = store.register(tmp_path / "up.jpg", size_bytes=1000, captured_at=old)
    store.set_upload_state(up.id, "done")           # uploaded, not starred → deletable
    pend = store.register(tmp_path / "pend.jpg", size_bytes=1000, captured_at=old)  # un-uploaded → protected
    star = store.register(tmp_path / "star.jpg", size_bytes=1000, captured_at=old)
    store.set_upload_state(star.id, "done")
    store.set_starred(star.id, True)                # uploaded + starred → protected

    # Force emergency and never drop below target, so the loop runs the
    # full candidate set — the exact condition that used to nuke everything.
    monkeypatch.setattr(policy, "disk_usage_pct", lambda _p: 96.0)

    report = policy.sweep(force=True)
    assert report.emergency_mode is True

    assert store.get(up.id) is None, "uploaded, unstarred archive photo should be freed"
    assert store.get(pend.id) is not None, "UN-UPLOADED photo must survive emergency — data-loss guard"
    assert store.get(star.id) is not None, "starred photo must survive emergency"


def test_set_starred_persists(fresh_db: Any, tmp_path: Path) -> None:
    from arclap_station.photos.store import get_store  # noqa: PLC0415

    store = get_store()
    p = store.register(tmp_path / "a.jpg", size_bytes=10, captured_at=datetime.now(UTC))
    assert store.set_starred(p.id, True) is True
    # The retention query reads COALESCE(starred,0); confirm it round-trips.
    from arclap_station.db import get_db  # noqa: PLC0415
    with get_db().connect() as conn:
        row = conn.execute("SELECT starred FROM photos WHERE id=?", (p.id,)).fetchone()
    assert int(row[0]) == 1
    assert store.set_starred(999999, True) is False  # missing photo
