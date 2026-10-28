"""Semantic layer: one YAML file per database (SPEC 4, format of data/cloudmetrics.yaml)."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(Exception):
    """The semantic layer file is missing, malformed or inconsistent."""


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    description: str = ""
    primary_key: bool = False
    references: str | None = None  # "table.column"


@dataclass(frozen=True)
class Table:
    name: str
    description: str
    columns: tuple[Column, ...]


@dataclass(frozen=True)
class SemanticLayer:
    name: str
    display_name: str
    path: str  # as written; may contain ${VAR}
    fiscal_year_start_month: int
    description: str
    tables: tuple[Table, ...]
    version: str  # hash of the file contents
    issues: tuple[str, ...] = field(default=())

    def resolve_db_path(self, env: dict[str, str] | None = None) -> Path:
        """Expand ${VAR} in `path`; a missing variable is an error, never an empty string."""
        env = os.environ if env is None else env

        def sub(match: re.Match[str]) -> str:
            var = match.group(1)
            if var not in env:
                raise ConfigError(f"Environment variable {var} is not set")
            return env[var]

        return Path(_ENV_REF.sub(sub, self.path))


def _require(data: dict, key: str, where: str):
    if key not in data:
        raise ConfigError(f"{where}: missing '{key}'")
    return data[key]


def load_semantic_layer(path: str | Path) -> SemanticLayer:
    raw = Path(path).read_bytes()
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: not valid YAML ({exc})") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")

    tables = []
    for t in _require(data, "tables", str(path)):
        where = f"table {t.get('name', '?')}"
        cols = tuple(
            Column(
                name=_require(c, "name", where),
                type=_require(c, "type", where),
                description=c.get("description", ""),
                primary_key=bool(c.get("primary_key", False)),
                references=c.get("references"),
            )
            for c in _require(t, "columns", where)
        )
        tables.append(Table(_require(t, "name", where), t.get("description", ""), cols))

    layer = SemanticLayer(
        name=_require(data, "name", str(path)),
        display_name=data.get("display_name", data["name"]),
        path=_require(data, "path", str(path)),
        fiscal_year_start_month=int(data.get("calendar", {}).get("fiscal_year_start_month", 1)),
        description=_require(data, "description", str(path)),
        tables=tuple(tables),
        version=hashlib.sha256(raw).hexdigest()[:12],
    )
    check_references(layer)
    return layer


def check_references(layer: SemanticLayer) -> None:
    """Every `references: table.column` must point at a column defined in the file."""
    known = {(t.name, c.name) for t in layer.tables for c in t.columns}
    for t in layer.tables:
        for c in t.columns:
            if c.references is None:
                continue
            target = tuple(c.references.split(".", 1))
            if target not in known:
                raise ConfigError(f"{t.name}.{c.name} references unknown {c.references}")


def check_against_database(layer: SemanticLayer, conn: sqlite3.Connection) -> SemanticLayer:
    """Drop tables/columns the live database no longer matches and report each one.

    A column is dropped when it is missing or its declared type differs. A column whose
    `references` target was dropped is dropped too, so the model never sees a dangling link.
    """
    issues: list[str] = []
    live: dict[str, dict[str, str]] = {}
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        live[table] = {
            r[1]: (r[2] or "").upper() for r in conn.execute(f'PRAGMA table_info("{table}")')
        }

    kept: list[Table] = []
    for t in layer.tables:
        if t.name not in live:
            issues.append(f"table {t.name} is missing from the database")
            continue
        cols = []
        for c in t.columns:
            actual = live[t.name].get(c.name)
            if actual is None:
                issues.append(f"{t.name}.{c.name} is missing from the database")
            elif actual != c.type.upper():
                issues.append(f"{t.name}.{c.name} has type {actual}, expected {c.type}")
            else:
                cols.append(c)
        kept.append(replace(t, columns=tuple(cols)))

    # Drop columns whose reference target did not survive; repeat until stable.
    changed = True
    while changed:
        changed = False
        known = {(t.name, c.name) for t in kept for c in t.columns}
        for i, t in enumerate(kept):
            cols = []
            for c in t.columns:
                if c.references and tuple(c.references.split(".", 1)) not in known:
                    issues.append(f"{t.name}.{c.name} points at unavailable {c.references}")
                    changed = True
                else:
                    cols.append(c)
            kept[i] = replace(t, columns=tuple(cols))

    return replace(layer, tables=tuple(kept), issues=tuple(issues))


def render_model_text(layer: SemanticLayer) -> str:
    """Exactly what the model sees: `description` verbatim, then tables as CREATE TABLE DDL."""
    parts = [layer.description.rstrip("\n"), ""]
    for t in layer.tables:
        if t.description:
            parts.append(f"-- {t.description}")
        lines = []
        for i, c in enumerate(t.columns):
            sql = f"  {c.name} {c.type}"
            if c.primary_key:
                sql += " PRIMARY KEY"
            if c.references:
                ref_table, ref_col = c.references.split(".", 1)
                sql += f" REFERENCES {ref_table}({ref_col})"
            sql += "," if i < len(t.columns) - 1 else ""
            if c.description:
                sql += f" -- {c.description}"
            lines.append(sql)
        parts.append(f"CREATE TABLE {t.name} (")
        parts.extend(lines)
        parts.append(");")
        parts.append("")
    return "\n".join(parts)
