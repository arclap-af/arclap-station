"""WebSocket MJPEG preview streamer (~8-12 fps from gp_capture_preview).

Circuit-breaker logic: when the camera is unhealthy, we don't hammer
the adapter at full FPS. Errors trigger exponential backoff up to 30 s
and we log them at INFO once on transition, not WARNING every cycle.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from arclap_station.camera.adapter import CameraAdapter, get_adapter

log = logging.getLogger(__name__)

DEFAULT_FPS = 10
MIN_FPS = 2
MAX_FPS = 15

# Circuit breaker: when the preview throws, double the wait until we
# stop trying so hard. Cap at 30 s — at that cadence a freshly-plugged
# camera will still wake up the stream within half a minute, without
# burning CPU + the camera's PTP buffer while it's absent.
BREAKER_INITIAL_SEC = 1.0
BREAKER_MAX_SEC = 30.0


async def preview_frames(adapter: CameraAdapter, fps: int = DEFAULT_FPS) -> AsyncIterator[bytes]:
    """Async generator that yields JPEG bytes at the requested fps."""
    fps = max(MIN_FPS, min(MAX_FPS, fps))
    delay = 1.0 / fps
    loop = asyncio.get_event_loop()
    breaker = BREAKER_INITIAL_SEC
    in_failure = False
    while True:
        try:
            frame = await loop.run_in_executor(None, adapter.capture_preview)
        except Exception as exc:  # noqa: BLE001 - libgphoto2 transient errors
            if not in_failure:
                # Log once on transition, not every frame — turning a
                # 10 fps stream into 10 WARNING lines per second when
                # the camera is absent fills journald in minutes.
                log.info("preview stalled: %s — backing off", exc)
                in_failure = True
            await asyncio.sleep(breaker)
            breaker = min(BREAKER_MAX_SEC, breaker * 2)
            continue
        if in_failure:
            log.info("preview recovered")
            in_failure = False
        breaker = BREAKER_INITIAL_SEC
        yield frame
        await asyncio.sleep(delay)


async def serve_preview_ws(ws: WebSocket, fps: int = DEFAULT_FPS) -> None:
    """Send binary JPEG frames over an already-accepted WebSocket."""
    adapter = get_adapter()
    try:
        async for frame in preview_frames(adapter, fps=fps):
            await ws.send_bytes(frame)
    except WebSocketDisconnect:
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("preview ws failed: %s", exc)
        try:
            await ws.close(code=1011)
        except Exception:  # noqa: BLE001
            pass
