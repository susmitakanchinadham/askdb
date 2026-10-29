"""Roles and the role-filtered semantic layer (SPEC 6).

`layer_for_role` is the only place a layer is narrowed. Everything the model sees, and every
statement it proposes, is derived from the narrowed layer, so a hidden table or column is not
mentioned in the prompt (not even in the free-text description) and cannot pass the validator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from askdb.config import ConfigError, SemanticLayer, Table


@dataclass(frozen=True)
class Role:
    name: str
    description: str
    allowed: dict[str, frozenset[str]]  # table -> visible columns, lowercase


@dataclass(frozen=True)
class RoleBook:
    database: str
    roles: dict[str, Role]

    def get(self, name: str) -> Role | None:
        return self.roles.get(name)


def load_roles(path: str | Path, layer: SemanticLayer) -> RoleBook:
    """Parse the roles file and check every name against the semantic layer."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot read roles file {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("roles"), dict) or not data["roles"]:
        raise ConfigError("Roles file needs a non-empty 'roles' map")
    if data.get("database") != layer.name:
        raise ConfigError(f"Roles file is for {data.get('database')!r}, not {layer.name!r}")

    columns = {t.name.lower(): [c.name.lower() for c in t.columns] for t in layer.tables}
    roles: dict[str, Role] = {}
    for name, spec in data["roles"].items():
        if not isinstance(spec, dict) or "tables" not in spec:
            raise ConfigError(f"Role {name!r} needs 'tables'")
        roles[name] = Role(
            name, str(spec.get("description", "")), _resolve(name, spec["tables"], columns)
        )
    return RoleBook(layer.name, roles)


def _resolve(role: str, tables: object, columns: dict[str, list[str]]) -> dict[str, frozenset[str]]:
    if tables == "*":
        return {t: frozenset(c) for t, c in columns.items()}
    if not isinstance(tables, dict) or not tables:
        raise ConfigError(f"Role {role!r}: 'tables' must be \"*\" or a non-empty map")
    out: dict[str, frozenset[str]] = {}
    for table, cols in tables.items():
        key = str(table).lower()
        if key not in columns:
            raise ConfigError(f"Role {role!r}: unknown table {table!r}")
        if cols == "*":
            out[key] = frozenset(columns[key])
            continue
        if not isinstance(cols, list) or not cols:
            raise ConfigError(f'Role {role!r}: columns of {table!r} must be "*" or a list')
        wanted = [str(c).lower() for c in cols]
        missing = [c for c in wanted if c not in columns[key]]
        if missing:
            raise ConfigError(f"Role {role!r}: unknown column(s) {missing} on {table!r}")
        out[key] = frozenset(wanted)
    return out


def hidden_terms(layer: SemanticLayer, role: Role) -> frozenset[str]:
    """Names of tables and columns the role cannot see.

    A hidden column whose name is also visible somewhere (such as `status`) is left out, since
    mentioning it reveals nothing and removing it would gut the visible text.
    """
    visible_columns = {c for cols in role.allowed.values() for c in cols}
    terms: set[str] = set()
    for table in layer.tables:
        key = table.name.lower()
        if key not in role.allowed:
            terms.add(key)
            if key.endswith("s"):
                terms.add(key[:-1])  # "invoices" also hides "invoice"
        for column in table.columns:
            name = column.name.lower()
            if name not in visible_columns and name not in role.allowed.get(key, ()):
                terms.add(name)
    return frozenset(terms)


def _mentions(text: str, terms: frozenset[str]) -> bool:
    return (
        bool(terms)
        and re.search(
            r"\b(?:" + "|".join(map(re.escape, sorted(terms))) + r")\b", text, re.IGNORECASE
        )
        is not None
    )


def redact_description(description: str, terms: frozenset[str]) -> str:
    """Drop each paragraph, bullet (with continuation lines) or heading naming a hidden term."""
    if not terms:
        return description
    blocks: list[list[list[str]]] = []  # block -> items -> lines
    for raw_block in re.split(r"\n\s*\n", description.strip("\n")):
        items: list[list[str]] = []
        for line in raw_block.split("\n"):
            if line.lstrip().startswith("- ") or not items:
                items.append([line])
            else:  # continuation of a bullet, or the rest of a paragraph
                items[-1].append(line)
        blocks.append(items)

    kept_blocks: list[str] = []
    for items in blocks:
        kept = [i for i in items if not _mentions("\n".join(i), terms)]
        has_bullets = any(i[0].lstrip().startswith("- ") for i in items)
        kept_bullets = [i for i in kept if i[0].lstrip().startswith("- ")]
        if has_bullets and not kept_bullets:
            continue  # a heading whose list is gone
        if kept:
            kept_blocks.append("\n".join("\n".join(i) for i in kept))
    return "\n\n".join(kept_blocks) + "\n"


def layer_for_role(layer: SemanticLayer, role: Role) -> SemanticLayer:
    """The layer as this role may see it: fewer tables and columns, redacted free text."""
    terms = hidden_terms(layer, role)
    tables: list[Table] = []
    for table in layer.tables:
        visible = role.allowed.get(table.name.lower())
        if not visible:
            continue
        columns = []
        for column in table.columns:
            if column.name.lower() not in visible:
                continue
            ref_table = column.references.split(".", 1)[0].lower() if column.references else None
            references = column.references if ref_table in role.allowed else None
            description = "" if _mentions(column.description, terms) else column.description
            columns.append(replace(column, references=references, description=description))
        description = "" if _mentions(table.description, terms) else table.description
        tables.append(Table(table.name, description, tuple(columns)))
    return replace(
        layer, tables=tuple(tables), description=redact_description(layer.description, terms)
    )
