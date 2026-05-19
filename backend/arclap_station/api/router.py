"""Top-level router that mounts every sub-router."""

from __future__ import annotations

from fastapi import APIRouter

from arclap_station.api.acceptance import router as acceptance_router
from arclap_station.api.auth import router as auth_router
from arclap_station.api.camera import router as camera_router
from arclap_station.api.destinations import router as destinations_router
from arclap_station.api.diag import router as diag_router
from arclap_station.api.gallery import router as gallery_router
from arclap_station.api.home import router as home_router
from arclap_station.api.queue import router as queue_router
from arclap_station.api.schedule import router as schedule_router
from arclap_station.api.settings import router as settings_router
from arclap_station.api.terminal import router as terminal_router
from arclap_station.setup_wizard import router as setup_router


def build_router() -> APIRouter:
    root = APIRouter()
    root.include_router(auth_router)
    root.include_router(setup_router)
    root.include_router(home_router)
    root.include_router(camera_router)
    root.include_router(gallery_router)
    root.include_router(schedule_router)
    root.include_router(destinations_router)
    root.include_router(queue_router)
    root.include_router(terminal_router)
    root.include_router(settings_router)
    root.include_router(acceptance_router)
    root.include_router(diag_router)
    return root
