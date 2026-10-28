import sqlite3
from pathlib import Path

import pytest

from askdb.config import (
    ConfigError,
    check_against_database,
    load_semantic_layer,
    render_model_text,
)

SNAPSHOT = Path(__file__).parent / "snapshots" / "cloudmetrics_model_text.txt"


def make_db(statements: list[str]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for s in statements:
        conn.execute(s)
    return conn


def write_yaml(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "db.yaml"
    p.write_text(text, encoding="utf-8")
    return p


MINI = """
name: mini
path: ${MINI_DB}
description: |
  Two tables.
tables:
  - name: parents
    description: Parents.
    columns:
      - { name: id, type: INTEGER, primary_key: true }
  - name: kids
    description: Kids.
    columns:
      - { name: id, type: INTEGER, primary_key: true }
      - { name: parent_id, type: INTEGER, references: parents.id, description: "Owner" }
      - { name: label, type: TEXT }
"""

MINI_DB = [
    "CREATE TABLE parents (id INTEGER PRIMARY KEY)",
    "CREATE TABLE kids (id INTEGER PRIMARY KEY, parent_id INTEGER, label TEXT)",
]


def test_loads_cloudmetrics(config_path):
    layer = load_semantic_layer(config_path)
    assert layer.name == "cloudmetrics"
    assert layer.display_name == "CloudMetrics SaaS Analytics"
    assert layer.path == "${CLOUDMETRICS_DB_PATH}"
    assert layer.fiscal_year_start_month == 1
    assert [t.name for t in layer.tables] == [
        "customers",
        "plans",
        "subscriptions",
        "invoices",
        "support_tickets",
    ]
    assert len(layer.version) == 12


def test_references_resolve_to_real_columns(config_path):
    layer = load_semantic_layer(config_path)
    cols = {(t.name, c.name) for t in layer.tables for c in t.columns}
    refs = [c.references for t in layer.tables for c in t.columns if c.references]
    assert len(refs) == 5
    assert all(tuple(r.split(".", 1)) in cols for r in refs)


def test_unknown_reference_is_rejected(tmp_path):
    p = write_yaml(tmp_path, MINI.replace("parents.id", "parents.nope"))
    with pytest.raises(ConfigError, match="kids.parent_id references unknown parents.nope"):
        load_semantic_layer(p)


def test_missing_required_key_is_rejected(tmp_path):
    p = write_yaml(tmp_path, "name: x\npath: y\ntables: []\n")
    with pytest.raises(ConfigError, match="description"):
        load_semantic_layer(p)


def test_version_changes_with_file_contents(tmp_path):
    a = load_semantic_layer(write_yaml(tmp_path, MINI))
    b = load_semantic_layer(write_yaml(tmp_path, MINI.replace("Kids.", "Children.")))
    assert a.version != b.version


def test_db_path_expands_env_and_requires_it(tmp_path):
    layer = load_semantic_layer(write_yaml(tmp_path, MINI))
    assert layer.resolve_db_path({"MINI_DB": "x/y.db"}) == Path("x/y.db")
    with pytest.raises(ConfigError, match="MINI_DB"):
        layer.resolve_db_path({})


def test_model_text_matches_snapshot(config_path):
    text = render_model_text(load_semantic_layer(config_path))
    assert text == SNAPSHOT.read_text(encoding="utf-8")


def test_model_text_starts_with_description_verbatim(config_path):
    layer = load_semantic_layer(config_path)
    assert render_model_text(layer).startswith(layer.description.rstrip("\n"))


def test_model_text_has_no_yaml_only_fields(config_path):
    text = render_model_text(load_semantic_layer(config_path))
    assert "CLOUDMETRICS_DB_PATH" not in text
    assert "fiscal_year_start_month" not in text


def test_matching_database_has_no_issues(config_path, db_path):
    layer = load_semantic_layer(config_path)
    conn = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    checked = check_against_database(layer, conn)
    assert checked.issues == ()
    assert checked.tables == layer.tables


def test_missing_column_is_removed_and_reported(tmp_path):
    layer = load_semantic_layer(write_yaml(tmp_path, MINI))
    conn = make_db(
        [
            "CREATE TABLE parents (id INTEGER PRIMARY KEY)",
            "CREATE TABLE kids (id INTEGER PRIMARY KEY, parent_id INTEGER)",
        ]
    )
    checked = check_against_database(layer, conn)
    kids = next(t for t in checked.tables if t.name == "kids")
    assert [c.name for c in kids.columns] == ["id", "parent_id"]
    assert checked.issues == ("kids.label is missing from the database",)
    assert "label" not in render_model_text(checked)


def test_type_mismatch_is_removed_and_reported(tmp_path):
    layer = load_semantic_layer(write_yaml(tmp_path, MINI))
    conn = make_db(
        [
            "CREATE TABLE parents (id INTEGER PRIMARY KEY)",
            "CREATE TABLE kids (id INTEGER PRIMARY KEY, parent_id INTEGER, label REAL)",
        ]
    )
    checked = check_against_database(layer, conn)
    assert checked.issues == ("kids.label has type REAL, expected TEXT",)


def test_missing_table_is_removed_with_dangling_references(tmp_path):
    layer = load_semantic_layer(write_yaml(tmp_path, MINI))
    conn = make_db(["CREATE TABLE kids (id INTEGER PRIMARY KEY, parent_id INTEGER, label TEXT)"])
    checked = check_against_database(layer, conn)
    assert [t.name for t in checked.tables] == ["kids"]
    kids = checked.tables[0]
    assert [c.name for c in kids.columns] == ["id", "label"]
    assert checked.issues == (
        "table parents is missing from the database",
        "kids.parent_id points at unavailable parents.id",
    )
