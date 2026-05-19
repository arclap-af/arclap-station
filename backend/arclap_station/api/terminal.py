"""Terminal router: /api/terminal/ws (WebSocket PTY)."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from arclap_station.terminal.pty import PTYNotSupported, RestrictedPTY
from arclap_station.terminal.pty import info as pty_info

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/terminal", tags=["terminal"])


@router.get("/info")
async def terminal_info() -> dict[str, object]:
    return pty_info()


@router.websocket("/ws")
async def terminal_ws(ws: WebSocket) -> None:
    # Auth BEFORE accept — the terminal is the highest-risk surface.
    from arclap_station.api.deps import require_ws_session  # noqa: PLC0415

    sess = await require_ws_session(ws)
    if sess is None:
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        pty = RestrictedPTY()
    except PTYNotSupported as exc:
        await ws.send_text(json.dumps({"type": "error", "msg": str(exc)}))
        await ws.close(code=1003)
        return

    try:
        pty.start()
    except Exception as exc:  # noqa: BLE001
        await ws.send_text(json.dumps({"type": "error", "msg": str(exc)}))
        await ws.close(code=1011)
        return

    async def reader() -> None:
        try:
            while True:
                data = await pty.read(4096)
                if not data:
                    break
                await ws.send_bytes(data)
        except Exception as exc:  # noqa: BLE001
            log.debug("pty reader stopped: %s", exc)

    reader_task = asyncio.create_task(reader())
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if "bytes" in msg and msg["bytes"] is not None:
                await pty.write(msg["bytes"])
            elif "text" in msg and msg["text"] is not None:
                try:
                    parsed = json.loads(msg["text"])
                except json.JSONDecodeError:
                    await pty.write(msg["text"].encode("utf-8"))
                    continue
                if parsed.get("type") == "resize":
                    pty.resize(int(parsed.get("rows", 24)), int(parsed.get("cols", 100)))
                elif parsed.get("type") == "input":
                    await pty.write(str(parsed.get("data", "")).encode("utf-8"))
    except WebSocketDisconnect:
        pass
    finally:
        reader_task.cancel()
        await pty.aclose()
