"""journalctl follow streamer (WebSocket-facing)."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from collections.abc import AsyncIterator
from typing import Any

from arclap_station.config import get_settings

log = logging.getLogger(__name__)


async def follow_journal(unit: str | None = None) -> AsyncIterator[dict[str, Any]]:
    """Yields decoded JSON log lines from journalctl -fu <unit> -o json."""
    unit = unit or get_settings().journal_unit
    if shutil.which("journalctl") is None:
        yield {
            "level": "warn",
            "msg": "journalctl not available on this platform — log stream is a no-op",
            "unit": unit,
        }
        # Keep the connection open with periodic heartbeats so the UI doesn't reconnect.
        while True:
            await asyncio.sleep(15)
            yield {"level": "info", "msg": "(heartbeat)", "unit": unit}

    proc = await asyncio.create_subprocess_exec(
        "journalctl",
        "-fu",
        unit,
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
                yield json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                yield {"raw": line.decode("utf-8", errors="replace").rstrip()}
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                proc.kill()
