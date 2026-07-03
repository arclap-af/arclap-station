"""Skip rules — disk > threshold, all destinations offline, time window, etc."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from arclap_station.config import get_settings
from arclap_station.uploaders.manager import get_manager

_DOW_LABELS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class SkipDecision:
    skip: bool
    reason: str | None = None


def should_skip(
    *,
    days_csv: str,
    from_time: str,
    to_time: str,
    dest_filter: str | None,
    now: datetime,
) -> SkipDecision:
    label = _DOW_LABELS[now.weekday()]
    days = {d.strip().lower() for d in days_csv.split(",") if d.strip()}
    if days and label not in days:
        return SkipDecision(True, f"day {label} not enabled")

    if from_time and to_time:
        try:
            f_h, f_m = (int(x) for x in from_time.split(":"))
            t_h, t_m = (int(x) for x in to_time.split(":"))
            cur = now.hour * 60 + now.minute
            f = f_h * 60 + f_m
            t = t_h * 60 + t_m
            in_window = f <= cur <= t if f <= t else (cur >= f or cur <= t)
            if not in_window:
                return SkipDecision(True, "outside time window")
        except ValueError:
            return SkipDecision(True, "invalid time window")

    settings = get_settings()
    photos_root = settings.paths.photos
    try:
        usage = shutil.disk_usage(photos_root if photos_root.exists() else photos_root.anchor)
        pct = (usage.used / usage.total) * 100
        if pct > settings.skip_disk_pct_threshold:
            return SkipDecision(True, f"disk usage {pct:.1f}% over threshold")
    except (OSError, ZeroDivisionError):
        pass

    if dest_filter:
        wanted_ids = {d.strip() for d in dest_filter.split(",") if d.strip()}
        manager = get_manager()
        relevant = [d for d in manager.list() if not wanted_ids or d.id in wanted_ids]
        if relevant and not any(d.enabled for d in relevant):
            return SkipDecision(True, "all matching destinations disabled")

    return SkipDecision(False)


def is_within_window(
    *,
    days_csv: str,
    from_time: str,
    to_time: str,
    now: datetime,
) -> bool:
    """True if ``now`` falls inside this schedule's active day + time window.

    Unlike :func:`should_skip`, this ignores the disk and destination
    gates — it answers only "should this schedule be capturing right
    now", which is what the camera auto-reconnect loop uses to decide
    whether the camera must be connected. (A full disk or an offline
    destination stops a *capture*, but the camera should still be up and
    ready during the window.)
    """
    label = _DOW_LABELS[now.weekday()]
    days = {d.strip().lower() for d in days_csv.split(",") if d.strip()}
    if days and label not in days:
        return False
    if from_time and to_time:
        try:
            f_h, f_m = (int(x) for x in from_time.split(":"))
            t_h, t_m = (int(x) for x in to_time.split(":"))
            cur = now.hour * 60 + now.minute
            f = f_h * 60 + f_m
            t = t_h * 60 + t_m
            return f <= cur <= t if f <= t else (cur >= f or cur <= t)
        except ValueError:
            return False
    return True


def list_destination_ids(dest_filter: str | None) -> list[str]:
    manager = get_manager()
    available = [d for d in manager.list() if d.enabled]
    if not dest_filter:
        return [d.id for d in available]
    wanted = {x.strip() for x in dest_filter.split(",") if x.strip()}
    return [d.id for d in available if d.id in wanted]


def humanize(d: SkipDecision) -> dict[str, Any]:
    return {"skip": d.skip, "reason": d.reason}
