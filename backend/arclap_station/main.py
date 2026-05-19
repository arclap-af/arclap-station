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
    import asyncio  # noqa: PLC0415

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

    # MQTT publisher — no-op if station isn't paired or cert is missing.
    try:
        from arclap_station.cloud.mqtt import get_publisher  # noqa: PLC0415

        mqtt = get_publisher()
        mqtt.start()
    except Exception as exc:  # noqa: BLE001
        log.warning("mqtt start skipped: %s", exc)
        mqtt = None

    # Periodic WAL checkpoint — without this the -wal sidecar grows
    # unbounded between retention sweeps (we saw 4 MB after a few hours).
    # PASSIVE mode never blocks writers; we don't need TRUNCATE here
    # because the nightly retention sweep already does that.
    async def _wal_checkpoint_loop() -> None:
        while True:
            await asyncio.sleep(900)  # 15 min
            try:
                with db.connect() as conn:
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except Exception as exc:  # noqa: BLE001
                log.debug("wal_checkpoint failed: %s", exc)

    wal_task = asyncio.create_task(_wal_checkpoint_loop())

    log.info("arclap-station started — etc=%s var=%s", settings.paths.etc, settings.paths.var)
    try:
        yield
    finally:
        wal_task.cancel()
        try:
            await wal_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        try:
            if mqtt is not None:
                mqtt.stop()
        except Exception:  # noqa: BLE001
            pass
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
        watchdog needs to call this without a session cookie.

        IMPORTANT: this endpoint must return in well under 1s even when
        the camera is unplugged. We therefore consult the health BEACON
        (a cross-process file written by camera ops) instead of opening
        a libgphoto2 handle. The service watchdog calls /api/health on a
        tight loop; making it touch the camera was a v0.5 regression
        that pushed every call to ~12s.
        """
        from arclap_station.camera import health as _ch  # noqa: PLC0415
        from arclap_station.db import get_db as _gdb  # noqa: PLC0415
        from arclap_station.telemetry.metrics import snapshot as _snap  # noqa: PLC0415
        from arclap_station.uploaders.queue import get_queue as _gq  # noqa: PLC0415

        db_ok = True
        try:
            with _gdb().connect() as c:
                c.execute("SELECT 1").fetchone()
        except Exception:  # noqa: BLE001
            db_ok = False
        # Camera state from the beacon — non-blocking, microsecond cost.
        cam_detected = _ch.is_fresh_and_ok()
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
    sub.add_parser(
        "exif-backfill",
        help="re-extract EXIF + dimensions for photos that lack them",
    )
    sub.add_parser(
        "backup",
        help="take a compressed snapshot of state.db (intended for daily timer)",
    )
    sub.add_parser(
        "db-integrity",
        help="run PRAGMA integrity_check on state.db (intended for weekly timer)",
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

    if cmd == "exif-backfill":
        return _exif_backfill()

    if cmd == "backup":
        from arclap_station.backup import run_backup  # noqa: PLC0415

        return run_backup()

    if cmd == "db-integrity":
        from arclap_station.backup import run_integrity  # noqa: PLC0415

        return run_integrity()

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


def _exif_backfill() -> int:
    """Re-read EXIF + dimensions for every photo missing them.

    Useful after upgrading from pre-v0.4 builds where scheduled captures
    skipped the EXIF extraction path. Idempotent — safe to run repeatedly.
    """
    import json as _json  # noqa: PLC0415
    from pathlib import Path as _Path  # noqa: PLC0415

    from arclap_station.db import get_db  # noqa: PLC0415
    from arclap_station.photos.exif import extract_exif  # noqa: PLC0415

    db = get_db()
    updated = 0
    skipped = 0
    missing = 0
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, path FROM photos "
            "WHERE exif_json IS NULL OR width IS NULL OR height IS NULL"
        ).fetchall()
    for r in rows:
        p = _Path(r["path"])
        if not p.exists():
            missing += 1
            continue
        exif, w, h = extract_exif(p)
        if not exif and w is None and h is None:
            skipped += 1
            continue
        with db.tx() as conn:
            conn.execute(
                "UPDATE photos SET exif_json=?, width=?, height=? WHERE id=?",
                (_json.dumps(exif) if exif else None, w, h, int(r["id"])),
            )
        updated += 1
    print(
        f"exif-backfill: updated={updated} skipped={skipped} "
        f"missing_files={missing} total_seen={len(rows)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(cli())
