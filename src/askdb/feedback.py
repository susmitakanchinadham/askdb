"""The "This looks wrong" reports (SPEC 8, 9): what an admin needs to reproduce the answer.

Stored in the same service-owned SQLite file as the audit log, never in the analytics database.
Like the audit log it holds a row count, not result values.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

MAX_NOTE = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  user TEXT NOT NULL,
  role TEXT NOT NULL,
  question TEXT NOT NULL,
  plan TEXT,
  sql TEXT NOT NULL,
  glossary_version TEXT NOT NULL,
  row_count INTEGER NOT NULL,
  note TEXT
)
"""


@dataclass(frozen=True)
class FeedbackRecord:
    user: str
    role: str
    question: str
    plan: str | None
    sql: str
    glossary_version: str
    row_count: int
    note: str | None = None


class FeedbackLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)

    def write(self, record: FeedbackRecord) -> None:
        note = record.note.strip()[:MAX_NOTE] if record.note else None
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO feedback (ts, user, role, question, plan, sql, glossary_version,"
                    " row_count, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        datetime.now(UTC).isoformat(timespec="seconds"),
                        record.user,
                        record.role,
                        record.question,
                        record.plan,
                        record.sql,
                        record.glossary_version,
                        record.row_count,
                        note or None,
                    ),
                )
        finally:
            conn.close()

    def rows(self) -> list[sqlite3.Row]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute("SELECT * FROM feedback ORDER BY id").fetchall()
        finally:
            conn.close()
