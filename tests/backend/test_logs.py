"""journald reader — normalisation, filtering, newest-first order."""

from __future__ import annotations

import asyncio
import datetime as _dt
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from arclap_station.telemetry.logs import (
    _matches,
    _normalise_journal_line,
    _priority_to_level,
    _resolve_unit,
    recent_journal,
)


# ---- normalisation ----------------------------------------------------------

def test_priority_levels_collapse() -> None:
    """RFC-5424 priorities collapse to info/warn/error.

    A previous version of the cockpit looked at the raw numeric
    PRIORITY and never matched any of the dropdown values; this
    regression ensures the three buckets stay stable.
    """
    assert _priority_to_level(0) == "error"
    assert _priority_to_level(3) == "error"
    assert _priority_to_level(4) == "warn"
    assert _priority_to_level(5) == "info"
    assert _priority_to_level(6) == "info"
    assert _priority_to_level(7) == "info"
    assert _priority_to_level("4") == "warn"
    assert _priority_to_level(None) == "info"
    assert _priority_to_level("garbage") == "info"


def test_normalise_journal_line_fills_all_fields() -> None:
    """Output must always carry ts/unit/level/message — never missing."""
    raw = {
        "__REALTIME_TIMESTAMP": "1779280800000000",
        "_SYSTEMD_UNIT": "arclap-station.service",
        "PRIORITY": 4,
        "MESSAGE": "camera reconnect requested",
    }
    out = _normalise_journal_line(raw)
    assert set(out.keys()) == {"ts", "unit", "level", "message"}
    assert out["level"] == "warn"
    assert out["unit"] == "arclap-station.service"
    assert out["message"] == "camera reconnect requested"
    # Timestamp: 1779280800000000 us = 2026-05-20T something UTC.
    assert out["ts"].startswith("2026-")


def test_normalise_falls_back_to_syslog_identifier() -> None:
    """If _SYSTEMD_UNIT is missing (rare but happens for early-boot), use SYSLOG_IDENTIFIER."""
    raw = {
        "__REALTIME_TIMESTAMP": "1779280800000000",
        "SYSLOG_IDENTIFIER": "kernel",
        "PRIORITY": 3,
        "MESSAGE": "USB device 4-2 reset",
    }
    out = _normalise_journal_line(raw)
    assert out["unit"] == "kernel"
    assert out["level"] == "error"


def test_normalise_falls_back_to_now_when_timestamp_missing() -> None:
    raw = {"_SYSTEMD_UNIT": "u", "MESSAGE": "x"}
    out = _normalise_journal_line(raw)
    # Should be a parseable ISO timestamp close to current time.
    parsed = _dt.datetime.fromisoformat(out["ts"])
    drift = abs((parsed - _dt.datetime.now(tz=_dt.UTC)).total_seconds())
    assert drift < 5.0


# ---- filtering --------------------------------------------------------------

def test_matches_level_filter() -> None:
    e = {"level": "warn", "message": "anything"}
    assert _matches(e, level="all", query=None)
    assert _matches(e, level="warn", query=None)
    assert not _matches(e, level="error", query=None)


def test_matches_query_is_case_insensitive() -> None:
    e = {"level": "info", "message": "Camera Reconnect Successful"}
    assert _matches(e, level=None, query="camera")
    assert _matches(e, level=None, query="SUCCESSFUL")
    assert not _matches(e, level=None, query="upload")


# ---- unit aliasing ----------------------------------------------------------

def test_resolve_unit_aliases() -> None:
    """Short names from the cockpit dropdown map to canonical units."""
    assert _resolve_unit("arclap-station") == "arclap-station.service"
    assert _resolve_unit("caddy") == "caddy.service"


def test_resolve_unit_all_defaults_to_main_service() -> None:
    """`all` is the cockpit's no-filter value; we default to the main service.

    Surfacing every journal entry (cron, snapd, kernel, etc.) on an
    operator view would be useless noise — the cockpit is for
    operating the station, not debugging the host OS.
    """
    target = _resolve_unit("all")
    assert "arclap-station" in target


# ---- recent_journal order ---------------------------------------------------

@pytest.mark.asyncio
async def test_recent_journal_returns_newest_first() -> None:
    """recent_journal() output must be sorted newest-first.

    journalctl emits oldest -> newest by default; the cockpit shows
    a newest-at-top timeline, so the backend pre-reverses the list
    instead of every consumer doing the sort.
    """
    # Three lines, journalctl emits them oldest -> newest.
    lines = [
        b'{"__REALTIME_TIMESTAMP":"1779000000000000","_SYSTEMD_UNIT":"u.service","PRIORITY":6,"MESSAGE":"old"}\n',
        b'{"__REALTIME_TIMESTAMP":"1779000060000000","_SYSTEMD_UNIT":"u.service","PRIORITY":6,"MESSAGE":"mid"}\n',
        b'{"__REALTIME_TIMESTAMP":"1779000120000000","_SYSTEMD_UNIT":"u.service","PRIORITY":6,"MESSAGE":"new"}\n',
    ]
    payload = b"".join(lines)

    class _FakeProc:
        def __init__(self) -> None:
            self.returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (payload, b"")

    with (
        patch("arclap_station.telemetry.logs.shutil.which", return_value="/usr/bin/journalctl"),
        patch(
            "arclap_station.telemetry.logs.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_FakeProc()),
        ),
    ):
        result = await recent_journal(unit="u", limit=10)

    assert len(result) == 3
    messages = [r["message"] for r in result]
    assert messages == ["new", "mid", "old"], (
        f"recent_journal must return newest-first, got {messages}"
    )


@pytest.mark.asyncio
async def test_recent_journal_applies_level_filter() -> None:
    """Server-side level filter must drop non-matching entries."""
    payload = (
        b'{"__REALTIME_TIMESTAMP":"1779000000000000","_SYSTEMD_UNIT":"u.service","PRIORITY":6,"MESSAGE":"info one"}\n'
        b'{"__REALTIME_TIMESTAMP":"1779000060000000","_SYSTEMD_UNIT":"u.service","PRIORITY":4,"MESSAGE":"warn one"}\n'
        b'{"__REALTIME_TIMESTAMP":"1779000120000000","_SYSTEMD_UNIT":"u.service","PRIORITY":3,"MESSAGE":"err one"}\n'
    )

    class _FakeProc:
        def __init__(self) -> None:
            self.returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (payload, b"")

    with (
        patch("arclap_station.telemetry.logs.shutil.which", return_value="/usr/bin/journalctl"),
        patch(
            "arclap_station.telemetry.logs.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_FakeProc()),
        ),
    ):
        result = await recent_journal(unit="u", level="error", limit=10)

    assert [r["message"] for r in result] == ["err one"]


@pytest.mark.asyncio
async def test_recent_journal_returns_empty_when_journalctl_missing() -> None:
    """Dev machines without journalctl get an empty list, not a crash."""
    with patch("arclap_station.telemetry.logs.shutil.which", return_value=None):
        result = await recent_journal(limit=10)
    assert result == []
