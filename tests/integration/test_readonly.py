import sqlite3

import pytest
from fastapi.testclient import TestClient

from askdb.db import open_readonly
from askdb.main import create_app


@pytest.fixture
def conn(db_path):
    c = open_readonly(db_path)
    yield c
    c.close()


def test_reads_work(conn):
    assert conn.execute("SELECT COUNT(*) FROM customers").fetchone() == (200,)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO plans (plan_name) VALUES ('x')",
        "UPDATE plans SET plan_name = 'x'",
        "DELETE FROM plans",
        "DROP TABLE plans",
        "CREATE TABLE extra (a INTEGER)",
    ],
)
def test_writes_fail_at_engine_level(conn, statement):
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute(statement)


def test_attach_is_refused_and_creates_no_file(conn, tmp_path):
    target = tmp_path / "new.db"
    with pytest.raises(sqlite3.OperationalError, match="attached"):
        conn.execute(f"ATTACH DATABASE '{target}' AS other")
    assert not target.exists()


def test_query_only_is_on(conn):
    assert conn.execute("PRAGMA query_only").fetchone() == (1,)


def test_missing_file_is_not_created(tmp_path):
    target = tmp_path / "absent.db"
    with pytest.raises(sqlite3.OperationalError):
        open_readonly(target)
    assert not target.exists()


def test_health(config_path, db_path, monkeypatch):
    monkeypatch.setenv("CLOUDMETRICS_DB_PATH", str(db_path))
    with TestClient(create_app(config_path)) as client:
        body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "CloudMetrics SaaS Analytics"
    assert body["issues"] == []
    assert len(body["version"]) == 12
