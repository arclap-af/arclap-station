"""SQLite state database — schema, migrations, and a thin connection wrapper.

We use a sync sqlite3 connection in WAL mode. APScheduler holds its own
SQLAlchemy session against a separate DB (scheduler.db) so this module only
owns `state.db`.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# Current schema version. Bump when adding a migration.
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS photos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    path          TEXT NOT NULL UNIQUE,
    captured_at   TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    width         INTEGER,
    height        INTEGER,
    exif_json     TEXT,
    job_id        TEXT,
    upload_state  TEXT NOT NULL DEFAULT 'pending',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_photos_captured_at ON photos(captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_photos_upload_state ON photos(upload_state);

CREATE TABLE IF NOT EXISTS upload_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id      INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    dest_id       TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'pending',  -- pending, in_flight, ok, failed
    attempts      INTEGER NOT NULL DEFAULT 0,
    next_at       TEXT NOT NULL DEFAULT (datetime('now')),
    last_error    TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_uq_state_next ON upload_queue(state, next_at);
CREATE INDEX IF NOT EXISTS idx_uq_photo ON upload_queue(photo_id);

CREATE TABLE IF NOT EXISTS schedules (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    interval_min  INTEGER NOT NULL,
    from_time     TEXT NOT NULL,  -- HH:MM
    to_time       TEXT NOT NULL,
    days_csv      TEXT NOT NULL,  -- e.g. "mon,tue,wed"
    enabled       INTEGER NOT NULL DEFAULT 1,
    dest_filter   TEXT,            -- csv of dest ids, NULL = all
    conditions    TEXT,            -- JSON of conditions
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS destinations (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    type          TEXT NOT NULL,
    config_json   TEXT NOT NULL,  -- encrypted-at-rest
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_ok_at    TEXT,
    last_error    TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL DEFAULT (datetime('now')),
    actor         TEXT NOT NULL,
    event         TEXT NOT NULL,
    details_json  TEXT,
    prev_hash     TEXT,
    hash          TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC);

CREATE TABLE IF NOT EXISTS acceptance_runs (
    id            TEXT PRIMARY KEY,
    started_at    TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at   TEXT,
    state         TEXT NOT NULL DEFAULT 'running',
    total_checks  INTEGER NOT NULL DEFAULT 0,
    pass_count    INTEGER NOT NULL DEFAULT 0,
    fail_count    INTEGER NOT NULL DEFAULT 0,
    report_json   TEXT
);

CREATE TABLE IF NOT EXISTS acceptance_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL REFERENCES acceptance_runs(id) ON DELETE CASCADE,
    group_name    TEXT NOT NULL,
    check_name    TEXT NOT NULL,
    state         TEXT NOT NULL,  -- ok, fail, skip, running
    detail        TEXT,
    duration_ms   INTEGER,
    finished_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_acc_results_run ON acceptance_results(run_id);

CREATE TABLE IF NOT EXISTS tokens (
    id            TEXT PRIMARY KEY,
    purpose       TEXT NOT NULL,
    value         TEXT NOT NULL,
    expires_at    TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Database:
    """Thread-safe sync SQLite wrapper with WAL and short-lived connections."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._tls = threading.local()

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._path,
            isolation_level=None,  # autocommit, we'll use BEGIN/COMMIT explicitly
            check_same_thread=False,
            timeout=10.0,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Yields a per-thread connection."""
        conn = getattr(self._tls, "conn", None)
        if conn is None:
            conn = self._connect()
            self._tls.conn = conn
        yield conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Yields a connection inside a transaction."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def initialise(self) -> None:
        """Apply schema. Idempotent.

        We can't run executescript() inside an explicit BEGIN — it issues its
        own COMMITs and the outer ROLLBACK would fail. So we apply the schema
        outside a transaction and then bump schema_version in one.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
        with self.tx() as conn:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )

    def close(self) -> None:
        conn = getattr(self._tls, "conn", None)
        if conn is not None:
            conn.close()
            self._tls.conn = None


_db_singleton: Database | None = None
_db_lock = threading.Lock()


def get_db(path: Path | None = None) -> Database:
    """Process-wide DB singleton."""
    global _db_singleton
    with _db_lock:
        if _db_singleton is None or (path is not None and _db_singleton.path != path):
            if path is None:
                from arclap_station.config import get_settings

                path = get_settings().paths.state_db
            db = Database(path)
            db.initialise()
            _db_singleton = db
    return _db_singleton


def reset_db_singleton() -> None:
    """Test hook."""
    global _db_singleton
    with _db_lock:
        if _db_singleton is not None:
            _db_singleton.close()
        _db_singleton = None
