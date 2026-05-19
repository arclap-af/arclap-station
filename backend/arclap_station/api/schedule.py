"""Schedule router: /api/schedule/*."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from arclap_station.api.deps import require_session
from arclap_station.audit import emit as audit_emit
from arclap_station.scheduler.engine import get_engine

router = APIRouter(prefix="/api/schedule", tags=["schedule"])

VALID_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


class ScheduleCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    interval_min: int = Field(..., ge=1, le=1440)
    from_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    to_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    days: list[str] = Field(default_factory=lambda: list(VALID_DAYS))
    enabled: bool = True
    dest_filter: str | None = None


class ScheduleUpdateRequest(BaseModel):
    name: str | None = None
    interval_min: int | None = Field(default=None, ge=1, le=1440)
    from_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    to_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    days: list[str] | None = None
    enabled: bool | None = None
    dest_filter: str | None = None


def _validate_days(days: list[str]) -> list[str]:
    cleaned = [d.strip().lower() for d in days if d.strip()]
    unknown = [d for d in cleaned if d not in VALID_DAYS]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown days: {unknown}"
        )
    return cleaned


@router.get("/list")
async def list_schedules(_: dict[str, Any] = Depends(require_session)) -> list[dict[str, Any]]:
    return [s.to_dict() for s in get_engine().list()]


@router.post("/create")
async def create_schedule(
    payload: ScheduleCreateRequest,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    days = _validate_days(payload.days)
    sched = get_engine().create(
        name=payload.name,
        interval_min=payload.interval_min,
        from_time=payload.from_time,
        to_time=payload.to_time,
        days=days,
        enabled=payload.enabled,
        dest_filter=payload.dest_filter,
    )
    audit_emit("user", "schedule.create", {"id": sched.id, "name": sched.name})
    return sched.to_dict()


@router.put("/{sched_id}")
async def update_schedule(
    sched_id: str,
    payload: ScheduleUpdateRequest,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    days = _validate_days(payload.days) if payload.days is not None else None
    updated = get_engine().update(
        sched_id,
        name=payload.name,
        interval_min=payload.interval_min,
        from_time=payload.from_time,
        to_time=payload.to_time,
        days=days,
        enabled=payload.enabled,
        dest_filter=payload.dest_filter,
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule not found")
    audit_emit("user", "schedule.update", {"id": sched_id})
    return updated.to_dict()


@router.delete("/{sched_id}")
async def delete_schedule(
    sched_id: str,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    if not get_engine().delete(sched_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule not found")
    audit_emit("user", "schedule.delete", {"id": sched_id})
    return {"ok": True}
