"""SQLite-backed photo metadata store."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arclap_station.db import Database, get_db


def _list_where(
    date: str | None,
    upload_filter: str | None,
    query: str | None,
) -> tuple[str, list[Any]]:
    """Build the WHERE clause for list() + count() so both stay in sync.

    Returns (where_sql, params). The leading space + WHERE / AND is
    included so callers can just concatenate after `FROM photos`.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if date:
        # Sargable range instead of date(captured_at)=date(?), so the
        # idx_photos_captured_at index is used rather than a full scan.
        clauses.append("captured_at >= ? AND captured_at < date(?, '+1 day')")
        params.extend([date, date])
    if upload_filter and upload_filter != "all":
        f = upload_filter.lower()
        if f == "uploaded":
            clauses.append("upload_state = 'done'")
        elif f == "pending":
            # Anything that hasn't finished uploading: queued, in
            # flight, transient-failed (will be retried), or stuck
            # without a queue entry. Matches the cockpit's pill name.
            clauses.append(
                "upload_state IN ('pending', 'in_progress', 'failed', 'failed_permanent')"
            )
        elif f == "starred":
            clauses.append("starred = 1")
        # any unknown filter just falls through to no constraint
    if query:
        like = f"%{query}%"
        clauses.append("(path LIKE ? OR CAST(id AS TEXT) LIKE ?)")
        params.extend([like, like])
    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


@dataclass
class PhotoRecord:
    id: int
    path: str
    captured_at: str
    size_bytes: int
    width: int | None
    height: int | None
    exif_json: str | None
    job_id: str | None
    upload_state: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        exif: dict[str, Any] | None = None
        if self.exif_json:
            try:
                exif = json.loads(self.exif_json)
            except json.JSONDecodeError:
                exif = None
        return {
            "id": self.id,
            "path": self.path,
            "filename": Path(self.path).name,
            "captured_at": self.captured_at,
            "size_bytes": self.size_bytes,
            "width": self.width,
            "height": self.height,
            "exif": exif,
            "job_id": self.job_id,
            "upload_state": self.upload_state,
            "created_at": self.created_at,
        }


class PhotoStore:
    def __init__(self, db: Database | None = None) -> None:
        self._db = db or get_db()

    def register(
        self,
        path: Path,
        size_bytes: int | None = None,
        captured_at: datetime | None = None,
        exif: dict[str, Any] | None = None,
        width: int | None = None,
        height: int | None = None,
        job_id: str | None = None,
    ) -> PhotoRecord:
        size = size_bytes if size_bytes is not None else path.stat().st_size
        ts = (captured_at or datetime.now(UTC)).isoformat()
        exif_json = json.dumps(exif) if exif else None

        with self._db.tx() as conn:
            cur = conn.execute(
                """
                INSERT INTO photos(path, captured_at, size_bytes, width, height,
                                   exif_json, job_id, upload_state)
                VALUES(?, ?, ?, ?, ?, ?, ?, 'pending')
                ON CONFLICT(path) DO UPDATE SET
                    size_bytes = excluded.size_bytes,
                    captured_at = excluded.captured_at
                RETURNING id
                """,
                (str(path), ts, size, width, height, exif_json, job_id),
            )
            row = cur.fetchone()
            assert row is not None
            new_id = int(row[0])
        return self.get(new_id)  # type: ignore[return-value]

    def get(self, photo_id: int) -> PhotoRecord | None:
        with self._db.connect() as conn:
            row = conn.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
        if row is None:
            return None
        return _row_to_record(row)

    def by_path(self, path: Path) -> PhotoRecord | None:
        with self._db.connect() as conn:
            row = conn.execute("SELECT * FROM photos WHERE path=?", (str(path),)).fetchone()
        if row is None:
            return None
        return _row_to_record(row)

    def list(
        self,
        limit: int = 100,
        offset: int = 0,
        date: str | None = None,
        upload_filter: str | None = None,
        query: str | None = None,
    ) -> list[PhotoRecord]:
        """List photos with optional filters.

        Filters compose. `upload_filter` narrows by upload_state
        (uploaded / pending / starred), `query` matches a substring
        of path / filename. The cockpit's pill bar + search box bind
        directly to these.
        """
        sql, params = _list_where(date, upload_filter, query)
        sql = "SELECT * FROM photos" + sql + " ORDER BY captured_at DESC LIMIT ? OFFSET ?"
        params = [*params, limit, offset]
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_record(r) for r in rows]

    def count(
        self,
        upload_filter: str | None = None,
        query: str | None = None,
    ) -> int:
        sql, params = _list_where(None, upload_filter, query)
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM photos" + sql, params
            ).fetchone()
        return int(row[0]) if row else 0

    def count_since(self, since: datetime) -> int:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM photos WHERE captured_at >= ?", (since.isoformat(),)
            ).fetchone()
        return int(row[0]) if row else 0

    def delete(self, photo_id: int, *, remove_file: bool = True) -> bool:
        record = self.get(photo_id)
        if record is None:
            return False
        with self._db.tx() as conn:
            conn.execute("DELETE FROM photos WHERE id=?", (photo_id,))
        if remove_file:
            try:
                Path(record.path).unlink(missing_ok=True)
            except OSError:
                pass
        return True

    def set_upload_state(self, photo_id: int, state: str) -> None:
        with self._db.tx() as conn:
            conn.execute("UPDATE photos SET upload_state=? WHERE id=?", (state, photo_id))


def _row_to_record(row: Any) -> PhotoRecord:
    return PhotoRecord(
        id=int(row["id"]),
        path=str(row["path"]),
        captured_at=str(row["captured_at"]),
        size_bytes=int(row["size_bytes"]),
        width=row["width"],
        height=row["height"],
        exif_json=row["exif_json"],
        job_id=row["job_id"],
        upload_state=str(row["upload_state"]),
        created_at=str(row["created_at"]),
    )


_store: PhotoStore | None = None


def get_store() -> PhotoStore:
    global _store
    if _store is None:
        _store = PhotoStore()
    return _store


def reset_store_singleton() -> None:
    global _store
    _store = None
