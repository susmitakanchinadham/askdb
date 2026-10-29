"""SQLite authorizer (SPEC 5.1): the engine-level second check behind the validator.

It allows exactly the tables and columns the validator saw, plus a short list of functions, and
denies everything else (writes, PRAGMA, ATTACH, recursion, extension loading, unknown functions).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Collection

Authorizer = Callable[[int, str | None, str | None, str | None, str | None], int]

# Names as SQLite reports them, which can differ from the validator's (SUBSTRING is `substr`).
ALLOWED_FUNCTIONS = frozenset(
    {
        "abs", "avg", "ceil", "ceiling", "coalesce", "count", "date", "datetime", "dense_rank",
        "floor", "glob", "group_concat", "ifnull", "iif", "instr", "julianday", "length",
        "like", "lower", "ltrim", "max", "min", "nullif", "rank", "replace", "round",
        "row_number", "rtrim", "sign", "strftime", "substr", "substring", "sum", "time",
        "total", "trim", "unixepoch", "upper",
    }
)  # fmt: skip


def make_authorizer(tables: Collection[str], columns: Collection[tuple[str, str]]) -> Authorizer:
    """Build a callback allowing reads of exactly `tables` and `columns` (case-insensitive)."""
    allowed_tables = {t.lower() for t in tables}
    allowed_columns = {(t.lower(), c.lower()) for t, c in columns}

    def authorize(action, arg1, arg2, dbname, source):  # noqa: ANN001
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table = (arg1 or "").lower()
            column = (arg2 or "").lower()
            if dbname not in (None, "main") or table not in allowed_tables:
                return sqlite3.SQLITE_DENY
            # An empty column name is how SQLite reports row-only access such as COUNT(*).
            if column == "" or (table, column) in allowed_columns:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            known = (arg2 or "").lower() in ALLOWED_FUNCTIONS
            return sqlite3.SQLITE_OK if known else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    return authorize
