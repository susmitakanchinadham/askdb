"""Read-only connection to the analytics database (SPEC 5.1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

BUSY_TIMEOUT_MS = 5000


def open_readonly(path: str | Path) -> sqlite3.Connection:
    """Open the file with `mode=ro` and `PRAGMA query_only=ON`. Never creates a missing file."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.execute("PRAGMA query_only = ON")
    # query_only does not stop ATTACH, which would create a new file on disk.
    conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    return conn
