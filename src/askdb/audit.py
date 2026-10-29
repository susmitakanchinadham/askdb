"""Audit log (SPEC 9): one row per request, never any result values, only a row count.

This is a separate, writable SQLite file owned by the service. It is never the analytics
database, which is only ever opened read-only. Retention is an open decision (SPEC 13.6): nothing
here deletes rows.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

OUTCOMES = ("answer", "clarify", "refuse", "error")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  user TEXT NOT NULL,
  role TEXT NOT NULL,
  question TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('answer', 'clarify', 'refuse', 'error')),
  reason TEXT,
  plan TEXT,
  sql TEXT,
  glossary_version TEXT,
  row_count INTEGER,
  duration_ms INTEGER NOT NULL,
  validator_result TEXT
)
"""


@dataclass(frozen=True)
class AuditRecord:
    user: str
    role: str
    question: str
    outcome: str  # answer | clarify | refuse | error
    duration_ms: int
    reason: str | None = None  # refusal reason, error kind
    plan: str | None = None  # interpretation or clarifying question, as shown to the user
    sql: str | None = None  # generated (or last rejected) statement; admins only
    glossary_version: str | None = None
    row_count: int | None = None
    validator_result: str | None = None  # "ok" or the checker's code


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)

    def write(self, record: AuditRecord) -> None:
        if record.outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {record.outcome!r}")
        conn = self._connect()
        try:
            with conn:  # commits on success
                conn.execute(
                    "INSERT INTO audit (ts, user, role, question, outcome, reason, plan, sql,"
                    " glossary_version, row_count, duration_ms, validator_result)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        datetime.now(UTC).isoformat(timespec="seconds"),
                        record.user,
                        record.role,
                        record.question,
                        record.outcome,
                        record.reason,
                        record.plan,
                        record.sql,
                        record.glossary_version,
                        record.row_count,
                        record.duration_ms,
                        record.validator_result,
                    ),
                )
        finally:
            conn.close()

    def rows(self) -> list[sqlite3.Row]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute("SELECT * FROM audit ORDER BY id").fetchall()
        finally:
            conn.close()
