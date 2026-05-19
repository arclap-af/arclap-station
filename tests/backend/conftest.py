"""Shared pytest fixtures.

Every test gets a fully isolated temp filesystem layout (ARCLAP_DEV_ROOT) so
the SQLite DBs, /etc/arclap, and the photos directory all live under a
tmp_path. The python-gphoto2 dependency is replaced by MockCamera.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

# Ensure the backend package is importable as `arclap_station`.
_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point every config path at a per-test temp dir and force MockCamera."""
    monkeypatch.setenv("ARCLAP_DEV_ROOT", str(tmp_path))
    monkeypatch.setenv("ARCLAP_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("ARCLAP_BIND_PORT", "8089")
    monkeypatch.setenv("ARCLAP_MOCK_CAMERA", "1")

    # Reset all module-level singletons so the new env vars take effect.
    from arclap_station import config as cfg_mod  # noqa: PLC0415
    cfg_mod.reset_settings_cache()

    from arclap_station import db as db_mod  # noqa: PLC0415
    db_mod.reset_db_singleton()

    from arclap_station.camera import adapter as cam_mod  # noqa: PLC0415
    from arclap_station.camera.mock import MockCamera  # noqa: PLC0415
    cam_mod.set_adapter(cam_mod.CameraAdapter(MockCamera()))

    from arclap_station.photos import store as ps_mod  # noqa: PLC0415
    ps_mod.reset_store_singleton()

    from arclap_station.scheduler import engine as eng_mod  # noqa: PLC0415
    eng_mod.reset_engine_singleton()

    from arclap_station.uploaders import manager as mgr_mod  # noqa: PLC0415
    mgr_mod.reset_manager_singleton()

    from arclap_station.uploaders import queue as q_mod  # noqa: PLC0415
    q_mod.reset_queue_singleton()

    from arclap_station.station_config import reset_station_store  # noqa: PLC0415
    reset_station_store()

    from arclap_station.acceptance.runner import reset_runner_singleton  # noqa: PLC0415
    reset_runner_singleton()

    yield tmp_path

    # Final cleanup — shut down the scheduler if a test left it running.
    eng_mod.reset_engine_singleton()
    q_mod.reset_queue_singleton()


@pytest.fixture()
def fresh_db() -> Any:
    """Return the initialised Database singleton (env already isolated)."""
    from arclap_station.db import get_db  # noqa: PLC0415

    return get_db()


@pytest.fixture()
def app() -> Iterator[Any]:
    """Build a fresh FastAPI app per test."""
    # Re-import so create_app uses the patched singletons.
    if "arclap_station.main" in sys.modules:
        importlib.reload(sys.modules["arclap_station.main"])
    from arclap_station.main import create_app  # noqa: PLC0415

    application = create_app()
    yield application


@pytest.fixture()
def client(app: Any) -> Iterator[Any]:
    from fastapi.testclient import TestClient  # noqa: PLC0415

    with TestClient(app) as c:
        yield c
