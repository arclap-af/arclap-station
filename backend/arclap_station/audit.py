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


def _anchor_path() -> Any:
    from arclap_station.config import get_settings  # noqa: PLC0415

    return get_settings().paths.var / "audit_anchor.json"


def _load_anchor() -> tuple[int, str | None]:
    """Return (last_pruned_id, last_pruned_hash) or (0, None) if the log
    has never been pruned."""
    try:
        d = json.loads(_anchor_path().read_text(encoding="utf-8"))
        return int(d["id"]), (str(d["hash"]) if d.get("hash") else None)
    except Exception:  # noqa: BLE001
        return 0, None


def _save_anchor(anchor_id: int, anchor_hash: str | None) -> None:
    p = _anchor_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"id": anchor_id, "hash": anchor_hash}), encoding="utf-8")
    tmp.replace(p)


def prune(keep: int = 50_000, db: Database | None = None) -> int:
    """Bound the audit log to the newest `keep` rows.

    The log is a hash chain, so we can't just delete the head — the first
    retained row's prev_hash would then point at nothing. Instead we save
    an ANCHOR (id + hash of the last row before the retained window);
    verify_chain() starts from that anchor so the retained window still
    verifies as a continuous chain. Called from the nightly retention
    sweep so a long-running station's audit_log can't grow without bound.
    """
    database = db or get_db()
    with database.connect() as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])
    if total <= keep:
        return 0
    with database.tx() as conn:
        cutoff = conn.execute(
            "SELECT id, hash FROM audit_log ORDER BY id DESC LIMIT 1 OFFSET ?",
            (keep,),
        ).fetchone()
        if cutoff is None:
            return 0
        cutoff_id, cutoff_hash = int(cutoff[0]), cutoff[1]
        deleted = conn.execute(
            "DELETE FROM audit_log WHERE id <= ?", (cutoff_id,)
        ).rowcount
    _save_anchor(cutoff_id, cutoff_hash)
    return deleted


def verify_chain(db: Database | None = None, page_size: int = 5000) -> dict[str, Any]:
    """Re-hash EVERY row (paginated) and report breaks.

    Previously this walked only the first 1000 rows — a tamperer could
    edit anything past row 1000 and the verifier would still say OK.
    Now we walk the whole table in `page_size` chunks, carrying the
    running `prev_hash` across pages so the chain stays continuous.
    """
    database = db or get_db()
    breaks: list[dict[str, Any]] = []
    # Start from the prune anchor (if the log has been bounded) so the
    # retained window verifies as a continuous chain instead of failing
    # at its first row, whose prev_hash points at a pruned row.
    last_id, prev = _load_anchor()
    checked = 0
    while True:
        with database.connect() as conn:
            rows = conn.execute(
                "SELECT id, ts, actor, event, details_json, prev_hash, hash "
                "FROM audit_log WHERE id > ? ORDER BY id ASC LIMIT ?",
                (last_id, page_size),
            ).fetchall()
        if not rows:
            break
        for r in rows:
            rid = int(r["id"])
            if (r["prev_hash"] or None) != (prev or None):
                breaks.append({"id": rid, "kind": "prev_mismatch"})
            expect = _hash(
                prev, str(r["ts"]), str(r["actor"]), str(r["event"]), r["details_json"]
            )
            if str(r["hash"]) != expect:
                breaks.append({"id": rid, "kind": "hash_mismatch"})
            prev = str(r["hash"]) if r["hash"] else None
            last_id = rid
            checked += 1
    return {"ok": not breaks, "breaks": breaks, "checked": checked}


def export_signed(
    *,
    start_id: int = 0,
    end_id: int | None = None,
    db: Database | None = None,
) -> dict[str, Any]:
    """Return a portable, hash-chain-signed export of the audit log.

    Bundle shape:
        {
          "station": {"serial": "...", "version": "..."},
          "generated_at": "<ISO8601>",
          "range": {"start_id": N, "end_id": M, "count": K},
          "entries": [...rows in id-asc order...],
          "chain_ok": true|false,
          "chain_breaks": [...],
          "fingerprint": "<sha256 of canonical JSON of entries>",
          "fingerprint_signed": "<base64 ed25519 sig>"  // present if key configured
        }

    The fingerprint alone is enough for a third party to detect
    tampering after the fact (recompute and compare). The signature
    requires the station's private key (`/etc/arclap/audit-export.key`,
    Ed25519, written at install time) and elevates the export to
    cryptographic non-repudiation. Without the key file, we still
    return the fingerprint.
    """
    import base64  # noqa: PLC0415
    import hashlib  # noqa: PLC0415
    from datetime import UTC as _UTC  # noqa: PLC0415
    from datetime import datetime as _dt

    from arclap_station import __version__  # noqa: PLC0415
    from arclap_station.config import get_settings  # noqa: PLC0415
    from arclap_station.station_config import get_station_store  # noqa: PLC0415

    database = db or get_db()
    end_clause = ""
    params: list[Any] = [int(start_id)]
    if end_id is not None:
        end_clause = " AND id <= ?"
        params.append(int(end_id))
    with database.connect() as conn:
        rows = conn.execute(
            f"SELECT id, ts, actor, event, details_json, prev_hash, hash "
            f"FROM audit_log WHERE id > ?{end_clause} ORDER BY id ASC",
            params,
        ).fetchall()
    entries: list[dict[str, Any]] = []
    for r in rows:
        details: Any = None
        if r["details_json"]:
            try:
                details = json.loads(r["details_json"])
            except json.JSONDecodeError:
                details = r["details_json"]
        entries.append({
            "id": int(r["id"]),
            "ts": str(r["ts"]),
            "actor": str(r["actor"]),
            "event": str(r["event"]),
            "details": details,
            "prev_hash": r["prev_hash"],
            "hash": r["hash"],
        })

    # Canonical JSON of the entries → fingerprint. Sort keys so the
    # fingerprint is reproducible across producers.
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    chain = verify_chain(db=database)
    cfg = get_station_store().load()
    bundle: dict[str, Any] = {
        "station": {
            "serial": cfg.serial,
            "hostname": cfg.hostname,
            "version": __version__,
        },
        "generated_at": _dt.now(_UTC).isoformat(),
        "range": {
            "start_id": int(start_id),
            "end_id": int(end_id) if end_id is not None else (entries[-1]["id"] if entries else 0),
            "count": len(entries),
        },
        "entries": entries,
        "chain_ok": chain["ok"],
        "chain_breaks": chain["breaks"],
        "fingerprint": fingerprint,
        "fingerprint_alg": "sha256",
    }

    # Optional Ed25519 signature over the fingerprint.
    key_path = get_settings().paths.etc / "audit-export.key"
    if key_path.exists():
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: PLC0415
                Ed25519PrivateKey,
            )
            from cryptography.hazmat.primitives.serialization import (  # noqa: PLC0415
                load_pem_private_key,
            )

            key = load_pem_private_key(key_path.read_bytes(), password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise TypeError("audit-export.key must be an Ed25519 private key")
            sig = key.sign(fingerprint.encode("ascii"))
            bundle["fingerprint_signed"] = base64.b64encode(sig).decode("ascii")
            bundle["fingerprint_alg"] = "sha256+ed25519"
        except Exception as exc:  # noqa: BLE001
            bundle["sign_error"] = str(exc)[:200]
    return bundle


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
