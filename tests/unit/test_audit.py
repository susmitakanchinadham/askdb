import sqlite3

import pytest

from askdb.audit import AuditLog, AuditRecord


def record(**kw):
    base = {
        "user": "u1",
        "role": "analyst",
        "question": "How many customers?",
        "outcome": "answer",
        "duration_ms": 12,
    }
    return AuditRecord(**{**base, **kw})


def test_row_holds_the_fields_the_spec_lists(tmp_path):
    log = AuditLog(tmp_path / "sub" / "audit.db")  # parent folder is created
    log.write(
        record(
            plan="Customer count",
            sql="SELECT COUNT(*) FROM customers",
            glossary_version="abc123",
            row_count=1,
            validator_result="ok",
        )
    )
    (row,) = log.rows()
    assert dict(row) | {"id": 0, "ts": ""} == {
        "id": 0,
        "ts": "",
        "user": "u1",
        "role": "analyst",
        "question": "How many customers?",
        "outcome": "answer",
        "reason": None,
        "plan": "Customer count",
        "sql": "SELECT COUNT(*) FROM customers",
        "glossary_version": "abc123",
        "row_count": 1,
        "duration_ms": 12,
        "validator_result": "ok",
    }
    assert row["ts"].endswith("+00:00")


@pytest.mark.parametrize("outcome", ["answer", "clarify", "refuse", "error"])
def test_every_outcome_is_accepted(tmp_path, outcome):
    log = AuditLog(tmp_path / "audit.db")
    log.write(record(outcome=outcome))
    assert [r["outcome"] for r in log.rows()] == [outcome]


def test_unknown_outcome_is_refused(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    with pytest.raises(ValueError):
        log.write(record(outcome="maybe"))
    assert log.rows() == []


def test_the_schema_itself_rejects_unknown_outcomes(tmp_path):
    AuditLog(tmp_path / "audit.db")
    conn = sqlite3.connect(tmp_path / "audit.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO audit (ts, user, role, question, outcome, duration_ms)"
            " VALUES ('t', 'u', 'r', 'q', 'maybe', 1)"
        )


def test_there_is_no_place_to_put_result_values(tmp_path):
    AuditLog(tmp_path / "audit.db")
    columns = [
        r[1] for r in sqlite3.connect(tmp_path / "audit.db").execute("PRAGMA table_info(audit)")
    ]
    assert not {"rows", "result", "values", "cells"} & set(columns)
    assert "row_count" in columns


def test_rows_are_appended_in_order_and_survive_reopening(tmp_path):
    path = tmp_path / "audit.db"
    AuditLog(path).write(record(question="first"))
    AuditLog(path).write(record(question="second"))
    assert [r["question"] for r in AuditLog(path).rows()] == ["first", "second"]
