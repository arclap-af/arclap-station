"""WebSocket MJPEG preview streamer (~8-12 fps from gp_capture_preview)."""

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


async def preview_frames(adapter: CameraAdapter, fps: int = DEFAULT_FPS) -> AsyncIterator[bytes]:
    """Async generator that yields JPEG bytes at the requested fps."""
    fps = max(MIN_FPS, min(MAX_FPS, fps))
    delay = 1.0 / fps
    loop = asyncio.get_event_loop()
    while True:
        try:
            frame = await loop.run_in_executor(None, adapter.capture_preview)
        except Exception as exc:  # noqa: BLE001 - libgphoto2 transient errors
            log.warning("preview frame error: %s", exc)
            await asyncio.sleep(0.5)
            continue
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
