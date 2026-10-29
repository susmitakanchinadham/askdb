"""Executor (SPEC 5.1, 5.2): run one validated statement with a deadline, a row ceiling and pages.

Only a successful `ValidationResult` is accepted, and its regenerated `sql` is what runs. Each call
opens its own short-lived read-only connection (`db.open_readonly`) guarded by the authorizer.
Pages are stateless: the statement gets a deterministic ordering and is re-run per page.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import sqlglot
from sqlglot import exp

from askdb.authorizer import make_authorizer
from askdb.db import open_readonly
from askdb.validator import DIALECT, ValidationResult

PAGE_SIZE = 25
ROW_CEILING = 1000
TIMEOUT_SECONDS = 10.0
MAX_CONCURRENT = 4
_PROGRESS_STEPS = 1000  # SQLite VM instructions between deadline checks


class ExecutionError(Exception):
    """Base class; messages are for logs, never for end users."""


class QueryTimeout(ExecutionError):
    pass


class TooBusy(ExecutionError):
    pass


@dataclass(frozen=True)
class Page:
    columns: list[str]
    rows: list[tuple]
    page: int
    page_size: int
    total: int  # rows available to page through, never above the ceiling
    truncated: bool  # more than the ceiling matched; the rest cannot be reached
    as_of: datetime

    @property
    def has_more(self) -> bool:
        return self.page * self.page_size < self.total


def ordered_sql(sql: str) -> str:
    """Append every result column (by position) to the ORDER BY so row order is deterministic.

    A statement with its own LIMIT is returned unchanged: it already caps the rows and extra
    keys could alter which ones it keeps.
    """
    root = sqlglot.parse_one(sql, dialect=DIALECT)
    if any(isinstance(e, exp.Star) for e in root.selects):
        raise ExecutionError("validated statement still contains a star")
    if isinstance(root, exp.Select) and (root.args.get("limit") or root.args.get("offset")):
        # Extra sort keys would change which rows the statement's own LIMIT keeps.
        return sql
    width = len(root.selects)
    if not isinstance(root, exp.Select):  # e.g. UNION: order the combined result
        root = exp.select("*").from_(root.subquery("u"))
    existing = root.args.get("order")
    keys = list(existing.expressions) if existing else []
    keys += [exp.Ordered(this=exp.Literal.number(i)) for i in range(1, width + 1)]
    return root.order_by(*keys, append=False, copy=True).sql(dialect=DIALECT)


class Executor:
    def __init__(
        self,
        db_path: str | Path,
        *,
        timeout: float = TIMEOUT_SECONDS,
        max_concurrent: int = MAX_CONCURRENT,
        row_ceiling: int = ROW_CEILING,
    ):
        self.db_path = db_path
        self.timeout = timeout
        self.row_ceiling = row_ceiling
        self._slots = threading.BoundedSemaphore(max_concurrent)

    def execute(self, validated: ValidationResult, page: int = 1) -> Page:
        if not validated.ok:
            raise ValueError("only a validated statement can be executed")
        if page < 1:
            raise ValueError("page starts at 1")
        if not self._slots.acquire(blocking=False):
            raise TooBusy("concurrency cap reached")
        try:
            return self._run(validated, page)
        finally:
            self._slots.release()

    def _run(self, validated: ValidationResult, page: int) -> Page:
        inner = ordered_sql(validated.sql)
        offset = (page - 1) * PAGE_SIZE
        limit = max(0, min(PAGE_SIZE, self.row_ceiling - offset))
        deadline = time.monotonic() + self.timeout

        conn = open_readonly(self.db_path)
        try:
            conn.set_authorizer(make_authorizer(validated.tables, validated.columns))
            conn.set_progress_handler(
                lambda: 1 if time.monotonic() > deadline else 0, _PROGRESS_STEPS
            )
            try:
                counted = conn.execute(
                    f"SELECT COUNT(*) FROM (SELECT 1 FROM ({validated.sql}) LIMIT ?)",
                    (self.row_ceiling + 1,),
                ).fetchone()[0]
                cursor = conn.execute(f"SELECT * FROM ({inner}) LIMIT ? OFFSET ?", (limit, offset))
                rows = cursor.fetchall()
                names = [d[0] for d in cursor.description]
            except sqlite3.OperationalError as exc:
                if "interrupted" in str(exc):
                    raise QueryTimeout("deadline passed") from exc
                raise ExecutionError(str(exc)) from exc
            except sqlite3.DatabaseError as exc:  # includes "not authorized"
                raise ExecutionError(str(exc)) from exc
        finally:
            conn.close()

        return Page(
            columns=names,
            rows=rows,
            page=page,
            page_size=PAGE_SIZE,
            total=min(counted, self.row_ceiling),
            truncated=counted > self.row_ceiling,
            as_of=datetime.now(UTC),
        )
