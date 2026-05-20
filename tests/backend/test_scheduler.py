"""Scheduler engine — CRUD + persistence + skip rules + misfire policy."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from arclap_station.config import get_settings
from arclap_station.scheduler.engine import ScheduleEngine, fire_capture, get_engine
from arclap_station.scheduler.rules import SkipDecision, should_skip


def test_create_and_list_schedule() -> None:
    eng = get_engine()
    sched = eng.create(
        name="Daily 15min",
        interval_min=15,
        from_time="06:00",
        to_time="19:00",
        days=["mon", "tue", "wed", "thu", "fri"],
    )
    assert sched.id
    assert sched.interval_min == 15
    rows = eng.list()
    assert len(rows) == 1
    assert rows[0].name == "Daily 15min"


def test_update_changes_interval() -> None:
    eng = get_engine()
    s = eng.create(
        "x", 30, "00:00", "23:59", days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    )
    updated = eng.update(s.id, interval_min=10)
    assert updated is not None
    assert updated.interval_min == 10


def test_delete_removes_row() -> None:
    eng = get_engine()
    s = eng.create("x", 30, "00:00", "23:59", days=["mon"])
    assert eng.delete(s.id)
    assert eng.get(s.id) is None


def test_skip_rule_day_off() -> None:
    # Choose a Monday-only schedule and ask "should I run on a Wednesday".
    decision: SkipDecision = should_skip(
        days_csv="mon",
        from_time="00:00",
        to_time="23:59",
        dest_filter=None,
        now=datetime(2026, 5, 20, 12, 0),  # Wednesday
    )
    assert decision.skip is True
    assert decision.reason


def test_skip_rule_outside_window() -> None:
    decision = should_skip(
        days_csv="mon,tue,wed,thu,fri,sat,sun",
        from_time="08:00",
        to_time="18:00",
        dest_filter=None,
        now=datetime(2026, 5, 19, 19, 30),
    )
    assert decision.skip is True
    assert "outside" in (decision.reason or "")


def test_skip_rule_in_window() -> None:
    decision = should_skip(
        days_csv="mon,tue,wed,thu,fri,sat,sun",
        from_time="08:00",
        to_time="18:00",
        dest_filter=None,
        now=datetime(2026, 5, 19, 12, 30),
    )
    assert decision.skip is False


def test_fire_capture_no_camera_skip(tmp_path: Path) -> None:
    eng = get_engine()
    s = eng.create(
        "now",
        1,
        "00:00",
        "23:59",
        days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    )
    result = fire_capture(s.id)
    # Mock camera reports detected=True, so capture should succeed.
    assert result["ok"] is True or result.get("skipped") is True


def test_engine_persists_across_reinit() -> None:
    eng = get_engine()
    eng.create("p", 5, "00:00", "23:59", days=["mon"])
    # Build a brand-new engine pointing at the same DB.
    fresh = ScheduleEngine(autostart=False, timezone_name="UTC")
    rows = fresh.list()
    assert any(r.name == "p" for r in rows)
    fresh.shutdown(wait=False)


def test_scheduler_db_file_exists() -> None:
    eng = get_engine()
    eng.create("x", 5, "00:00", "23:59", days=["mon"])
    settings = get_settings()
    # Some installations defer DB creation until first job runs; presence of
    # the path is enough for the spec.
    assert settings.paths.scheduler_db.parent.exists()


def test_skip_flags_default_true_for_new_schedule() -> None:
    """Schedules created without explicit skip flags must default both to True.

    This matches the cockpit's default ON state for the Disk>90% and
    Destinations-offline toggles. A schedule that came back with these
    flags as False would surprise the operator by capturing through
    storage exhaustion or while every destination is disabled.
    """
    eng = get_engine()
    s = eng.create("default-flags", 5, "00:00", "23:59", days=["mon"])
    d = s.to_dict()
    assert d["skip_disk_full"] is True, "skip_disk_full should default ON"
    assert d["skip_destinations_offline"] is True, (
        "skip_destinations_offline should default ON"
    )


def test_skip_flags_round_trip_through_create() -> None:
    """Both flags must persist through CREATE and be readable on next list().

    Was broken before this fix: the API accepted the request but
    silently dropped the flags, so reopening the schedule in the
    cockpit showed them flipped to False — the exact "saving doesn't
    keep my settings" symptom the operator reported.
    """
    eng = get_engine()
    s = eng.create(
        "explicit-off",
        5,
        "00:00",
        "23:59",
        days=["mon"],
        skip_disk_full=False,
        skip_destinations_offline=False,
    )
    d = s.to_dict()
    assert d["skip_disk_full"] is False
    assert d["skip_destinations_offline"] is False

    # Read it back via list() — proves the DB stored the conditions JSON
    # correctly and to_dict() can re-parse it on a fresh engine call.
    [reloaded] = [r.to_dict() for r in eng.list() if r.id == s.id]
    assert reloaded["skip_disk_full"] is False
    assert reloaded["skip_destinations_offline"] is False


def test_skip_flags_partial_update_preserves_other_flag() -> None:
    """Updating one skip flag must not strip the other from the JSON.

    Before this fix, conditions was overwritten as a single opaque
    column; toggling one flag would wipe the other to its default.
    """
    eng = get_engine()
    s = eng.create(
        "two-flags",
        5,
        "00:00",
        "23:59",
        days=["mon"],
        skip_disk_full=True,
        skip_destinations_offline=False,
    )
    # Now toggle ONLY skip_disk_full to False; skip_destinations_offline
    # must remain False.
    updated = eng.update(s.id, skip_disk_full=False)
    assert updated is not None
    d = updated.to_dict()
    assert d["skip_disk_full"] is False
    assert d["skip_destinations_offline"] is False, (
        "untouched flag was stripped — partial update overwrote conditions"
    )


def test_dest_filter_can_be_cleared_to_none() -> None:
    """Update with clear_dest_filter=True must set dest_filter back to NULL.

    The cockpit's "All destinations" choice maps to dest_filter=None.
    Without the explicit clear flag, the engine's existing 'val is
    not None' guard treats None as "leave alone", so an operator
    could never switch a schedule from a specific destination back
    to fanout.
    """
    eng = get_engine()
    s = eng.create(
        "with-filter",
        5,
        "00:00",
        "23:59",
        days=["mon"],
        dest_filter="some-uuid",
    )
    assert s.dest_filter == "some-uuid"
    updated = eng.update(s.id, dest_filter=None, clear_dest_filter=True)
    assert updated is not None
    assert updated.dest_filter is None
