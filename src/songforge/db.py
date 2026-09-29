"""SQLite job table. One row per song, cover or transcription job."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'song',
    title TEXT NOT NULL DEFAULT '',
    style TEXT NOT NULL DEFAULT '',
    lyrics TEXT NOT NULL DEFAULT '',
    instrumental INTEGER NOT NULL DEFAULT 0,
    mode TEXT NOT NULL DEFAULT 'fast',
    seed INTEGER NOT NULL DEFAULT 0,
    max_seconds INTEGER NOT NULL DEFAULT 240,
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    progress REAL NOT NULL DEFAULT 0,
    seconds REAL,
    wall_ms INTEGER,
    peak_rss INTEGER,
    peak_footprint INTEGER,
    path TEXT,
    abc TEXT,
    source_name TEXT,
    source_path TEXT,
    task TEXT,
    peaks TEXT,
    truncated INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS songs_status ON songs(status);
CREATE INDEX IF NOT EXISTS songs_created ON songs(created_at);
"""

MIGRATIONS = [("peak_footprint", "INTEGER")]

ACTIVE = ("queued", "running")
FINAL = ("done", "failed", "cancelled")

_UPDATABLE = {
    "title",
    "status",
    "stage",
    "detail",
    "progress",
    "seconds",
    "wall_ms",
    "peak_rss",
    "peak_footprint",
    "path",
    "abc",
    "peaks",
    "truncated",
    "error",
    "started_at",
    "finished_at",
    "source_path",
}


class Store:
    """Thread-safe wrapper. The worker thread and the request handlers share one connection."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a library was created."""
        have = {row[1] for row in self._conn.execute("PRAGMA table_info(songs)")}
        for name, decl in MIGRATIONS:
            if name not in have:
                self._conn.execute(f"ALTER TABLE songs ADD COLUMN {name} {decl}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create(self, **fields: Any) -> dict:
        row = {
            "id": fields.pop("id", None) or uuid.uuid4().hex[:12],
            "created_at": time.time(),
            **fields,
        }
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO songs ({columns}) VALUES ({marks})", list(row.values())
            )
            self._conn.commit()
        return self.get(row["id"])

    def get(self, song_id: str) -> dict | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM songs WHERE id = ?", (song_id,))
            row = cur.fetchone()
        return _to_dict(row) if row else None

    def list(self, kind: str | None = None, limit: int = 200) -> list[dict]:
        query = "SELECT * FROM songs"
        args: list[Any] = []
        if kind:
            query += " WHERE kind = ?"
            args.append(kind)
        query += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(query, args).fetchall()
        return [_to_dict(r) for r in rows]

    def update(self, song_id: str, **fields: Any) -> dict | None:
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"Cannot update fields: {sorted(unknown)}")
        if "peaks" in fields and not isinstance(fields["peaks"], (str, type(None))):
            fields["peaks"] = json.dumps(fields["peaks"])
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE songs SET {sets} WHERE id = ?", [*fields.values(), song_id])
            self._conn.commit()
        return self.get(song_id)

    def delete(self, song_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM songs WHERE id = ?", (song_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def next_queued(self) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM songs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
        return _to_dict(row) if row else None

    def queue_position(self, song_id: str) -> int:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM songs WHERE status = 'queued' ORDER BY created_at"
            ).fetchall()
        ids = [r["id"] for r in rows]
        return ids.index(song_id) + 1 if song_id in ids else 0

    def recover(self) -> int:
        """Mark jobs that were running when the server stopped as failed."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE songs SET status = 'failed', error = 'Interrupted: the server stopped', "
                "finished_at = ? WHERE status = 'running'",
                (time.time(),),
            )
            self._conn.commit()
        return cur.rowcount


def _to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["instrumental"] = bool(data["instrumental"])
    data["truncated"] = bool(data["truncated"])
    if data.get("peaks"):
        try:
            data["peaks"] = json.loads(data["peaks"])
        except ValueError:
            data["peaks"] = None
    return data
