"""FastAPI app factory + Uvicorn CLI entrypoint."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from arclap_station import __version__
from arclap_station.api import build_router
from arclap_station.config import get_settings
from arclap_station.db import get_db
from arclap_station.scheduler.engine import get_engine
from arclap_station.uploaders.queue import get_queue

log = logging.getLogger(__name__)


def _configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s — %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.paths.ensure()
    db = get_db()
    db.initialise()
    # Populate station.serial from /proc/cpuinfo on first boot (idempotent).
    try:
        from arclap_station.station_config import ensure_serial_from_cpu  # noqa: PLC0415

        ensure_serial_from_cpu()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not populate station serial: %s", exc)
    engine = get_engine()
    engine.hydrate_from_db()
    engine.start()
    queue = get_queue()
    queue.start()
    log.info("arclap-station started — etc=%s var=%s", settings.paths.etc, settings.paths.var)
    try:
        yield
    finally:
        try:
            queue.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            engine.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Arclap Station",
        version=__version__,
        lifespan=lifespan,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.include_router(build_router())

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        """Deep health probe used by the service watchdog AND any external
        monitor. Returns ok=False (HTTP still 200) if any essential
        subsystem looks unhealthy. Don't gate on auth — the loopback
        watchdog needs to call this without a session cookie."""
        from arclap_station.camera.adapter import get_adapter  # noqa: PLC0415
        from arclap_station.db import get_db as _gdb  # noqa: PLC0415
        from arclap_station.telemetry.metrics import snapshot as _snap  # noqa: PLC0415
        from arclap_station.uploaders.queue import get_queue as _gq  # noqa: PLC0415

        db_ok = True
        try:
            with _gdb().connect() as c:
                c.execute("SELECT 1").fetchone()
        except Exception:  # noqa: BLE001
            db_ok = False
        cam_detected = False
        try:
            cam_detected = bool(get_adapter().detect().detected)
        except Exception:  # noqa: BLE001
            cam_detected = False
        try:
            queue_depth = _gq().pending_depth()
        except Exception:  # noqa: BLE001
            queue_depth = -1
        snap = {}
        try:
            snap = _snap()
        except Exception:  # noqa: BLE001
            pass
        ok = db_ok and (snap.get("uptime_seconds", 0) > 0)
        return {
            "ok": ok,
            "version": __version__,
            "db_ok": db_ok,
            "camera_detected": cam_detected,
            "queue_pending": queue_depth,
            "disk_used_pct": snap.get("disk_used_pct"),
            "cpu_temp_c": snap.get("cpu_temp_c"),
            "uptime_seconds": snap.get("uptime_seconds"),
        }

    @app.get("/api/version")
    async def version() -> dict[str, Any]:
        return {"version": __version__}

    return app


# Module-level app for `uvicorn arclap_station.main:app`.
app = create_app()


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arclap-station")
    sub = parser.add_subparsers(dest="cmd", required=False)

    serve = sub.add_parser("serve", help="run the FastAPI server")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", default=None, type=int)
    serve.add_argument("--reload", action="store_true")
    serve.add_argument("--log-level", default="info")

    sub.add_parser("healthcheck", help="probe local /api/health")
    sub.add_parser("version", help="print version and exit")
    sub.add_parser(
        "camera-watchdog",
        help="run one camera USB watchdog probe (intended for the systemd timer)",
    )
    sub.add_parser(
        "retention-sweep",
        help="enforce disk-retention policy (intended for the systemd timer)",
    )

    args = parser.parse_args(argv)
    cmd = args.cmd or "serve"

    if cmd == "version":
        print(__version__)
        return 0

    if cmd == "camera-watchdog":
        from arclap_station.watchdog.camera import run as run_camera_watchdog  # noqa: PLC0415

        return run_camera_watchdog()

    if cmd == "retention-sweep":
        from arclap_station.retention.policy import run as run_retention  # noqa: PLC0415

        return run_retention()

    if cmd == "healthcheck":
        import httpx  # noqa: PLC0415

        settings = get_settings()
        url = f"http://{settings.bind_host}:{settings.bind_port}/api/health"
        try:
            r = httpx.get(url, timeout=3)
            print(json.dumps(r.json()))
            return 0 if r.status_code == 200 else 1
        except Exception as exc:  # noqa: BLE001
            print(f"healthcheck failed: {exc}", file=sys.stderr)
            return 2

    # serve
    settings = get_settings()
    host = args.host or settings.bind_host
    port = args.port or settings.bind_port
    _configure_logging(args.log_level.upper())
    import uvicorn  # noqa: PLC0415

    uvicorn.run(
        "arclap_station.main:app",
        host=host,
        port=port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    sys.exit(cli())
