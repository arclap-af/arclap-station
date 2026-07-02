"""Prometheus-compatible /metrics endpoint + FastAPI middleware.

Why hand-rolled instead of `prometheus_client`: keeping the deps lean
on the Pi (we already pull ~40 wheels). The output format is the same
plain-text exposition Prometheus + Grafana + VictoriaMetrics all read.

Tracks:
  - arclap_request_total{method,path,status}      counter
  - arclap_request_latency_seconds_bucket{le,...} histogram
  - arclap_request_latency_seconds_sum
  - arclap_request_latency_seconds_count
  - arclap_camera_health{state}                   gauge (1/0)
  - arclap_queue_depth                            gauge
  - arclap_queue_failed_total                     counter
  - arclap_disk_used_pct                          gauge
  - arclap_disk_free_bytes                        gauge
  - arclap_cpu_temp_celsius                       gauge
  - arclap_memory_used_bytes                      gauge
  - arclap_uptime_seconds                         gauge
  - arclap_audit_events_total                     counter (from db)

Buckets chosen to spot both fast happy-path (<10ms) and pathological
slow responses (>5s).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# Histogram buckets in seconds.
_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

_lock = threading.Lock()
# {(method, path, status): count}
_counters: dict[tuple[str, str, str], int] = {}
# {(method, path): {"sum": float, "count": int, "buckets": [int...]}}
_hist: dict[tuple[str, str], dict[str, Any]] = {}


def _path_label(path: str) -> str:
    """Normalize a request path so we don't blow up cardinality.

    A `/api/destinations/abc123` and `/api/destinations/def456` should
    aggregate to `/api/destinations/{id}` in the metrics output.
    """
    # Cheap: replace 32-char hex IDs and numeric IDs.
    import re  # noqa: PLC0415

    p = re.sub(r"/[0-9a-f]{32}\b", "/{id}", path)
    p = re.sub(r"/\d{2,}\b", "/{id}", p)
    return p[:128]


class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Don't instrument /metrics itself — would be useless self-traffic.
        if request.url.path == "/metrics":
            return await call_next(request)
        start = time.monotonic()
        try:
            response = await call_next(request)
            status = str(response.status_code)
        except Exception:
            duration = time.monotonic() - start
            _record(request.method, _path_label(request.url.path), "500", duration)
            raise
        duration = time.monotonic() - start
        _record(request.method, _path_label(request.url.path), status, duration)
        return response


def _record(method: str, path: str, status: str, duration_sec: float) -> None:
    key_count = (method, path, status)
    key_hist = (method, path)
    with _lock:
        _counters[key_count] = _counters.get(key_count, 0) + 1
        h = _hist.get(key_hist)
        if h is None:
            h = {"sum": 0.0, "count": 0, "buckets": [0] * len(_BUCKETS)}
            _hist[key_hist] = h
        h["sum"] += duration_sec
        h["count"] += 1
        for i, b in enumerate(_BUCKETS):
            if duration_sec <= b:
                h["buckets"][i] += 1


def render() -> str:
    """Return the full Prometheus text exposition."""
    from arclap_station import __version__  # noqa: PLC0415
    from arclap_station.camera import health as _ch  # noqa: PLC0415
    from arclap_station.db import get_db  # noqa: PLC0415
    from arclap_station.telemetry.metrics import snapshot as _snap  # noqa: PLC0415
    from arclap_station.uploaders.queue import get_queue  # noqa: PLC0415

    out: list[str] = []

    def line(s: str) -> None:
        out.append(s)

    # ---- request metrics ----
    line("# HELP arclap_request_total Total HTTP requests handled by the backend.")
    line("# TYPE arclap_request_total counter")
    with _lock:
        for (method, path, status), n in sorted(_counters.items()):
            line(f'arclap_request_total{{method="{method}",path="{path}",status="{status}"}} {n}')

        line("# HELP arclap_request_latency_seconds HTTP request latency histogram.")
        line("# TYPE arclap_request_latency_seconds histogram")
        for (method, path), h in sorted(_hist.items()):
            for b, count in zip(_BUCKETS, h["buckets"], strict=False):
                line(
                    f'arclap_request_latency_seconds_bucket{{method="{method}",path="{path}",le="{b}"}} {count}'
                )
            line(
                f'arclap_request_latency_seconds_bucket{{method="{method}",path="{path}",le="+Inf"}} {h["count"]}'
            )
            line(
                f'arclap_request_latency_seconds_sum{{method="{method}",path="{path}"}} {h["sum"]:.6f}'
            )
            line(
                f'arclap_request_latency_seconds_count{{method="{method}",path="{path}"}} {h["count"]}'
            )

    # ---- camera ----
    state = _ch.read_state()
    line("# HELP arclap_camera_ok 1 if the camera health beacon's last op succeeded.")
    line("# TYPE arclap_camera_ok gauge")
    line(f'arclap_camera_ok {1 if state.get("ok") else 0}')
    age = _ch.beacon_age_sec()
    if age is not None:
        line("# HELP arclap_camera_beacon_age_seconds Age of the camera health beacon write.")
        line("# TYPE arclap_camera_beacon_age_seconds gauge")
        line(f"arclap_camera_beacon_age_seconds {age:.2f}")

    # ---- queue ----
    try:
        q = get_queue()
        depth = q.pending_depth()
        stats = q.stats()
        line("# HELP arclap_queue_pending Items still to upload.")
        line("# TYPE arclap_queue_pending gauge")
        line(f"arclap_queue_pending {depth}")
        line("# HELP arclap_queue_state_total Items by terminal state.")
        line("# TYPE arclap_queue_state_total counter")
        for k, v in stats.items():
            line(f'arclap_queue_state_total{{state="{k}"}} {v}')
        avg = q.avg_upload_seconds()
        line("# HELP arclap_upload_avg_seconds Rolling average upload duration.")
        line("# TYPE arclap_upload_avg_seconds gauge")
        line(f"arclap_upload_avg_seconds {avg}")
    except Exception:  # noqa: BLE001
        pass

    # ---- system snapshot ----
    try:
        s = _snap()
        for key, prom_name in (
            ("cpu_temp_c", "arclap_cpu_temp_celsius"),
            ("cpu_pct", "arclap_cpu_pct"),
            ("mem_used_mb", "arclap_memory_used_megabytes"),
            ("mem_total_mb", "arclap_memory_total_megabytes"),
            ("disk_used_pct", "arclap_disk_used_pct"),
            ("disk_free_bytes", "arclap_disk_free_bytes"),
            ("uptime_seconds", "arclap_uptime_seconds"),
            ("network_throughput_mbps", "arclap_network_throughput_mbps"),
        ):
            val = s.get(key)
            if val is None:
                continue
            line(f"# TYPE {prom_name} gauge")
            line(f"{prom_name} {val}")
    except Exception:  # noqa: BLE001
        pass

    # ---- audit count ----
    try:
        with get_db().connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
        if row:
            line("# HELP arclap_audit_events_total Total rows in the audit log.")
            line("# TYPE arclap_audit_events_total counter")
            line(f"arclap_audit_events_total {int(row[0])}")
    except Exception:  # noqa: BLE001
        pass

    # ---- build info ----
    line("# HELP arclap_build_info Build metadata.")
    line("# TYPE arclap_build_info gauge")
    line(f'arclap_build_info{{version="{__version__}"}} 1')

    return "\n".join(out) + "\n"


def percentile_summary() -> dict[str, dict[str, float]]:
    """Return p50/p95 per endpoint for the cockpit's diag page.

    Approximated from the histogram buckets — the smallest bucket
    boundary whose cumulative count exceeds p50/p95 of total count.
    """
    out: dict[str, dict[str, float]] = {}
    with _lock:
        for (method, path), h in _hist.items():
            total = h["count"]
            if total == 0:
                continue
            p50_target = total * 0.5
            p95_target = total * 0.95
            running = 0
            p50: float | None = None
            p95: float | None = None
            for b, c in zip(_BUCKETS, h["buckets"], strict=False):
                running += c
                if p50 is None and running >= p50_target:
                    p50 = b
                if p95 is None and running >= p95_target:
                    p95 = b
            out[f"{method} {path}"] = {
                "count": total,
                "p50_s": p50 or _BUCKETS[-1],
                "p95_s": p95 or _BUCKETS[-1],
                "avg_s": h["sum"] / total,
            }
    return out
