"""journalctl readers — both history (one-shot) and live (follow).

Both readers emit the SAME normalised schema so the cockpit can mix
recent + streaming entries into a single sorted timeline:

    {
        "ts":      "2026-05-20T14:35:12.345Z",     # ISO 8601 UTC
        "unit":    "arclap-station.service",        # _SYSTEMD_UNIT
        "level":   "info" | "warn" | "error",       # collapsed PRIORITY
        "message": "Camera detect attempt 1/3 ...",  # MESSAGE
    }

PRIORITY collapsing (RFC 5424):
    0 emerg / 1 alert / 2 crit / 3 err          -> "error"
    4 warning                                    -> "warn"
    5 notice / 6 info / 7 debug                  -> "info"
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import shutil
from collections.abc import AsyncIterator
from typing import Any

from arclap_station.config import get_settings

log = logging.getLogger(__name__)


# Map of systemd-unit aliases the cockpit may send to canonical unit names.
# Lets the operator pick "arclap-station" without the ".service" suffix.
_UNIT_ALIASES = {
    "arclap-station": "arclap-station.service",
    "caddy": "caddy.service",
    "arclap-usb3-disable": "arclap-usb3-disable.service",
}


def _resolve_unit(unit: str | None) -> str:
    if not unit or unit == "all":
        # `journalctl -u arclap-station.service` is the default for "all"
        # because in this product the user only ever cares about the
        # main service. We deliberately don't surface kernel / cron /
        # everything — that's not a useful operator view.
        return get_settings().journal_unit
    return _UNIT_ALIASES.get(unit, unit)


def _priority_to_level(priority: Any) -> str:
    try:
        p = int(priority)
    except (TypeError, ValueError):
        return "info"
    if p <= 3:
        return "error"
    if p == 4:
        return "warn"
    return "info"


def _normalise_journal_line(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise one decoded journald JSON record to the cockpit shape."""
    # journald timestamps: __REALTIME_TIMESTAMP is microseconds since
    # epoch as a STRING. Fall back to the syslog timestamp or "now".
    ts_iso: str
    rt = raw.get("__REALTIME_TIMESTAMP")
    if rt:
        try:
            ts_iso = _dt.datetime.fromtimestamp(
                int(rt) / 1_000_000, tz=_dt.UTC
            ).isoformat()
        except (TypeError, ValueError):
            ts_iso = _dt.datetime.now(tz=_dt.UTC).isoformat()
    else:
        ts_iso = _dt.datetime.now(tz=_dt.UTC).isoformat()

    unit = (
        raw.get("_SYSTEMD_UNIT")
        or raw.get("SYSLOG_IDENTIFIER")
        or raw.get("unit")
        or "system"
    )
    return {
        "ts": ts_iso,
        "unit": str(unit),
        "level": _priority_to_level(raw.get("PRIORITY")),
        "message": str(raw.get("MESSAGE", raw.get("raw", ""))),
    }


def _matches(entry: dict[str, Any], level: str | None, query: str | None) -> bool:
    if level and level != "all" and entry["level"] != level:
        return False
    if query:
        if query.lower() not in entry["message"].lower():
            return False
    return True


async def recent_journal(
    unit: str | None = None,
    level: str | None = None,
    query: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """One-shot read of recent journal lines, normalised + newest-first.

    Uses `journalctl -n LIMIT --reverse` so we get the most recent
    entries even if the journal is huge. Server-side level filtering
    via `-p` would be nice but PRIORITY filter syntax requires a range;
    we filter post-decode instead, which keeps the code dead simple.
    """
    if shutil.which("journalctl") is None:
        return []
    target_unit = _resolve_unit(unit)
    # Cap to a sensible range — the WS will stream subsequent lines.
    capped_limit = max(10, min(1000, int(limit)))
    proc = await asyncio.create_subprocess_exec(
        "journalctl",
        "-u",
        target_unit,
        "-n",
        str(capped_limit),
        "-o",
        "json",
        "--no-pager",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    except TimeoutError:
        proc.kill()
        return []
    out: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line:
            continue
        try:
            decoded = json.loads(line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        norm = _normalise_journal_line(decoded)
        if _matches(norm, level, query):
            out.append(norm)
    # journalctl emits oldest -> newest; reverse so the cockpit shows
    # newest at top without each consumer having to re-sort.
    out.reverse()
    return out


async def follow_journal(unit: str | None = None) -> AsyncIterator[dict[str, Any]]:
    """Yields normalised log lines from `journalctl -fu <unit>`.

    Output shape matches `recent_journal()` so the cockpit can mix
    streaming entries with the history list using one schema.
    """
    target_unit = _resolve_unit(unit)
    if shutil.which("journalctl") is None:
        yield _normalise_journal_line({
            "PRIORITY": 4,
            "MESSAGE": "journalctl not available — log stream is a no-op",
            "_SYSTEMD_UNIT": target_unit,
        })
        # Keep the connection open with periodic heartbeats so the UI
        # doesn't reconnect.
        while True:
            await asyncio.sleep(15)
            yield _normalise_journal_line({
                "PRIORITY": 6,
                "MESSAGE": "(heartbeat)",
                "_SYSTEMD_UNIT": target_unit,
            })

    proc = await asyncio.create_subprocess_exec(
        "journalctl",
        "-fu",
        target_unit,
        "-o",
        "json",
        "--no-pager",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                decoded = json.loads(line.decode("utf-8", errors="replace"))
                yield _normalise_journal_line(decoded)
            except json.JSONDecodeError:
                # Fall back to a minimal record so the cockpit's
                # filter / order stay consistent.
                yield {
                    "ts": _dt.datetime.now(tz=_dt.UTC).isoformat(),
                    "unit": target_unit,
                    "level": "info",
                    "message": line.decode("utf-8", errors="replace").rstrip(),
                }
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                proc.kill()
