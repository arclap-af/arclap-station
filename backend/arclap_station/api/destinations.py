"""Destinations router: /api/destinations/*."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from arclap_station.api.deps import require_session
from arclap_station.audit import emit as audit_emit
from arclap_station.uploaders import REGISTRY, UploadError
from arclap_station.uploaders.manager import get_manager

router = APIRouter(prefix="/api/destinations", tags=["destinations"])


class DestinationCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    type: str
    config: dict[str, Any]
    enabled: bool = True


class DestinationUpdateRequest(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class DestinationTestRequest(BaseModel):
    type: str
    config: dict[str, Any]


@router.get("/list")
async def list_destinations(_: dict[str, Any] = Depends(require_session)) -> list[dict[str, Any]]:
    return [d.to_dict(redact=True) for d in get_manager().list()]


@router.get("/types")
async def list_types(_: dict[str, Any] = Depends(require_session)) -> list[str]:
    return sorted(REGISTRY.keys())


@router.post("/create")
async def create_destination(
    payload: DestinationCreateRequest,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    if payload.type not in REGISTRY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown destination type: {payload.type}",
        )
    # Validate the uploader can be constructed BEFORE we persist. The
    # individual uploaders raise ValueError when required config keys
    # are missing (e.g. `local` needs `path`, `ftp` needs `host`).
    # Without this guard the manager would persist an unconstructable
    # record and surface ValueError as a generic 500 on every
    # subsequent list() call.
    try:
        factory = REGISTRY[payload.type]
        probe = factory("probe", "probe", payload.config)
        probe.close()
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid {payload.type} config: {exc}",
        ) from exc
    dest = get_manager().create(
        name=payload.name,
        type_id=payload.type,
        config=payload.config,
        enabled=payload.enabled,
    )
    audit_emit("user", "destination.create", {"id": dest.id, "type": dest.type})
    return dest.to_dict(redact=True)


@router.post("/test")
async def test_destination(
    payload: DestinationTestRequest,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    if payload.type not in REGISTRY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="unknown destination type"
        )
    factory = REGISTRY[payload.type]
    # Construction itself can throw — the LocalUploader for example
    # raises ValueError("local uploader requires 'path'") when the
    # form sends an empty config. That used to bubble up as a
    # generic 500 (no useful info for the operator). Catch it here
    # and surface as 400 with the uploader's own message so the
    # cockpit banner shows "invalid local config: ..." instead of
    # the bare "HTTP 500 /api/destinations/test".
    try:
        uploader = factory("probe", "probe", payload.config)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid {payload.type} config: {exc}",
        ) from exc
    try:
        result = uploader.test()
    except UploadError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    finally:
        uploader.close()
    return result


@router.put("/{dest_id}")
async def update_destination(
    dest_id: str,
    payload: DestinationUpdateRequest,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    updated = get_manager().update(
        dest_id, name=payload.name, config=payload.config, enabled=payload.enabled
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    audit_emit("user", "destination.update", {"id": dest_id})
    return updated.to_dict(redact=True)


@router.delete("/{dest_id}")
async def delete_destination(
    dest_id: str,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    if not get_manager().delete(dest_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    audit_emit("user", "destination.delete", {"id": dest_id})
    return {"ok": True}
