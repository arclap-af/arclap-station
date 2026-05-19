"""Append-only audit log with hash-chain."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from arclap_station.db import Database, get_db


def _hash(prev: str | None, ts: str, actor: str, event: str, details_json: str | None) -> str:
    h = hashlib.sha256()
    h.update((prev or "").encode("utf-8"))
    h.update(b"\x00")
    h.update(ts.encode("utf-8"))
    h.update(b"\x00")
    h.update(actor.encode("utf-8"))
    h.update(b"\x00")
    h.update(event.encode("utf-8"))
    h.update(b"\x00")
    h.update((details_json or "").encode("utf-8"))
    return h.hexdigest()


def emit(
    actor: str,
    event: str,
    details: dict[str, Any] | None = None,
    *,
    db: Database | None = None,
) -> None:
    """Append a single audit row, computing hash-chain link."""
    database = db or get_db()
    details_json = json.dumps(details, default=str) if details else None
    with database.tx() as conn:
        prev = conn.execute(
            "SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev_hash = prev[0] if prev else None
        cur = conn.execute(
            """
            INSERT INTO audit_log(actor, event, details_json, prev_hash)
            VALUES(?, ?, ?, ?)
            RETURNING id, ts
            """,
            (actor, event, details_json, prev_hash),
        )
        row = cur.fetchone()
        new_id, ts = int(row[0]), str(row[1])
        chain = _hash(prev_hash, ts, actor, event, details_json)
        conn.execute("UPDATE audit_log SET hash=? WHERE id=?", (chain, new_id))


def verify_chain(db: Database | None = None, limit: int = 1000) -> dict[str, Any]:
    """Re-hash every row and report breaks. Returns {ok, breaks: [...]}."""
    database = db or get_db()
    with database.connect() as conn:
        rows = conn.execute(
            "SELECT id, ts, actor, event, details_json, prev_hash, hash "
            "FROM audit_log ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
    breaks: list[dict[str, Any]] = []
    prev: str | None = None
    for r in rows:
        rid = int(r["id"])
        if (r["prev_hash"] or None) != (prev or None):
            breaks.append({"id": rid, "kind": "prev_mismatch"})
        expect = _hash(prev, str(r["ts"]), str(r["actor"]), str(r["event"]), r["details_json"])
        if str(r["hash"]) != expect:
            breaks.append({"id": rid, "kind": "hash_mismatch"})
        prev = str(r["hash"]) if r["hash"] else None
    return {"ok": not breaks, "breaks": breaks, "checked": len(rows)}


def recent(limit: int = 100, db: Database | None = None) -> list[dict[str, Any]]:
    database = db or get_db()
    with database.connect() as conn:
        rows = conn.execute(
            "SELECT id, ts, actor, event, details_json, prev_hash, hash "
            "FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        details: Any = None
        if r["details_json"]:
            try:
                details = json.loads(r["details_json"])
            except json.JSONDecodeError:
                details = r["details_json"]
        out.append(
            {
                "id": int(r["id"]),
                "ts": str(r["ts"]),
                "actor": str(r["actor"]),
                "event": str(r["event"]),
                "details": details,
                "hash": r["hash"],
            }
        )
    return out
