"""Acceptance router: /api/acceptance/*."""

from __future__ import annotations

import io
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status

from arclap_station.acceptance.runner import get_runner
from arclap_station.api.deps import require_session
from arclap_station.audit import emit as audit_emit

router = APIRouter(prefix="/api/acceptance", tags=["acceptance"])


@router.post("/run")
async def run_acceptance(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    runner = get_runner()
    current = runner.current
    if current is not None:
        return {"ok": True, "run_id": current, "already_running": True}
    run_id = runner.start(background=True)
    audit_emit("user", "acceptance.user_triggered", {"run_id": run_id})
    return {"ok": True, "run_id": run_id}


@router.get("/status/{run_id}")
async def acceptance_status(
    run_id: str,
    _: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    summary = get_runner().status(run_id)
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return summary.to_dict()


@router.get("/latest")
async def acceptance_latest(_: dict[str, Any] = Depends(require_session)) -> dict[str, Any] | None:
    summary = get_runner().latest()
    return summary.to_dict() if summary else None


@router.get("/report.{ext}")
async def acceptance_report(
    ext: str,
    run_id: str | None = None,
    _: dict[str, Any] = Depends(require_session),
) -> Response:
    runner = get_runner()
    summary = runner.status(run_id) if run_id else runner.latest()
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no run yet")
    if ext == "json":
        return Response(json.dumps(summary.to_dict(), indent=2), media_type="application/json")
    if ext == "txt":
        return Response(_to_text(summary.to_dict()), media_type="text/plain")
    if ext == "pdf":
        return Response(_to_pdf(summary.to_dict()), media_type="application/pdf")
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported format")


def _to_text(data: dict[str, Any]) -> str:
    out = io.StringIO()
    out.write("Arclap Station — Acceptance Report\n")
    out.write(f"Run: {data['id']}\n")
    out.write(f"State: {data['state']}\n")
    out.write(f"Passed: {data['passed']} / {data['total']}\n")
    out.write(f"Failed: {data['failed']}\n")
    out.write(f"Started: {data['started_at']}\n")
    out.write(f"Finished: {data['finished_at']}\n")
    out.write("\n")
    for item in data["report"]:
        out.write(
            f"  [{item['state'].upper():4s}] {item['group']:12s} {item['check']:24s} "
            f"{item.get('detail') or ''}\n"
        )
    return out.getvalue()


def _to_pdf(data: dict[str, Any]) -> bytes:
    """Bare-minimum self-contained PDF — no external dep required."""
    text = _to_text(data).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = (
        "BT\n/F1 9 Tf\n40 800 Td\n14 TL\n"
        + "".join(f"({line}) Tj T*\n" for line in text.splitlines())
        + "ET"
    )
    objects: list[bytes] = []

    def add(obj: str) -> int:
        objects.append(obj.encode("latin-1", errors="replace"))
        return len(objects)

    add("<< /Type /Catalog /Pages 2 0 R >>")
    add("<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    add("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>")
    add(f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream")
    add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = b"%PDF-1.4\n"
    offsets: list[int] = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"
    xref_off = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_off}\n%%EOF\n"
    ).encode("latin-1")
    return out
