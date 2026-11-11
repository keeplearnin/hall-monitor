"""The desk's complaint store: SQLite, keyed by complaint ID, idempotent.

A record seen again with the same content is skipped; one whose content changed (a company
response arriving, say) is kept as a new version. Records that fail validation go to the
quarantine table with the field names and error types, never their values. The pull cursor
(CFPB's search_after value for the last record processed) is kept here, so a pull resumes
exactly where the last one stopped.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from hall_monitor.complaints.models import Complaint

DEFAULT_STORE = Path(__file__).resolve().parents[2] / "var" / "complaints" / "store.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS complaints (
    complaint_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    content_sha256 TEXT NOT NULL,
    pull_id TEXT NOT NULL,
    stored_at TEXT NOT NULL,
    record TEXT NOT NULL,
    PRIMARY KEY (complaint_id, version)
);
CREATE TABLE IF NOT EXISTS quarantine (
    raw_sha256 TEXT PRIMARY KEY,
    complaint_id TEXT,
    pull_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    quarantined_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pulls (
    pull_id TEXT PRIMARY KEY,
    summary TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

Outcome = Literal["new", "updated", "unchanged"]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ComplaintStore:
    def __init__(self, path: Path = DEFAULT_STORE):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> ComplaintStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def upsert(self, complaint: Complaint, pull_id: str) -> Outcome:
        digest = complaint.content_sha256()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT version, content_sha256 FROM complaints WHERE complaint_id = ?"
                    " ORDER BY version DESC LIMIT 1",
                    (complaint.complaint_id,),
                ).fetchone()
                if row is not None and row[1] == digest:
                    outcome: Outcome = "unchanged"
                else:
                    outcome = "new" if row is None else "updated"
                    self._db.execute(
                        "INSERT INTO complaints VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            complaint.complaint_id,
                            1 if row is None else row[0] + 1,
                            digest,
                            pull_id,
                            _now(),
                            complaint.model_dump_json(),
                        ),
                    )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return outcome

    def quarantine(
        self, pull_id: str, raw_sha256: str, complaint_id: str | None, reason: str
    ) -> bool:
        """Record a record that failed validation; False if this exact record already is."""
        with self._lock:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO quarantine VALUES (?, ?, ?, ?, ?)",
                (raw_sha256, complaint_id, pull_id, reason, _now()),
            )
        return cursor.rowcount == 1

    def complaints(self) -> list[Complaint]:
        """The latest version of every complaint, oldest received first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT c.record FROM complaints c JOIN (SELECT complaint_id, MAX(version) v"
                " FROM complaints GROUP BY complaint_id) m"
                " ON c.complaint_id = m.complaint_id AND c.version = m.v"
            ).fetchall()
        found = [Complaint.model_validate_json(r[0]) for r in rows]
        return sorted(found, key=lambda c: (c.date_received, c.complaint_id))

    def get(self, complaint_id: str) -> Complaint | None:
        with self._lock:
            row = self._db.execute(
                "SELECT record FROM complaints WHERE complaint_id = ? ORDER BY version DESC"
                " LIMIT 1",
                (complaint_id,),
            ).fetchone()
        return None if row is None else Complaint.model_validate_json(row[0])

    def quarantined(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT raw_sha256, complaint_id, pull_id, reason, quarantined_at"
                " FROM quarantine ORDER BY quarantined_at, raw_sha256"
            ).fetchall()
        keys = ("raw_sha256", "complaint_id", "pull_id", "reason", "quarantined_at")
        return [dict(zip(keys, r, strict=True)) for r in rows]

    def cursor(self) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM state WHERE key = 'cursor'").fetchone()
        return None if row is None else row[0]

    def set_cursor(self, cursor: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO state VALUES ('cursor', ?) ON CONFLICT(key) DO UPDATE SET value ="
                " excluded.value",
                (cursor,),
            )

    def record_pull(self, summary: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO pulls VALUES (?, ?)",
                (summary["pull_id"], json.dumps(summary, sort_keys=True)),
            )

    def pulls(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT summary FROM pulls ORDER BY pull_id").fetchall()
        return [json.loads(r[0]) for r in rows]

    def counts(self) -> dict[str, int]:
        with self._lock:
            complaints = self._db.execute(
                "SELECT COUNT(DISTINCT complaint_id), COUNT(*) FROM complaints"
            ).fetchone()
            quarantined = self._db.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0]
        return {
            "complaints": complaints[0],
            "versions": complaints[1],
            "quarantined": quarantined,
        }
