import sqlite3

import pytest

from askdb.authorizer import make_authorizer
from askdb.db import open_readonly

TABLES = {"customers"}
COLUMNS = {("customers", "customer_id"), ("customers", "industry")}


@pytest.fixture
def conn(db_path):
    c = open_readonly(db_path)
    c.set_authorizer(make_authorizer(TABLES, COLUMNS))
    yield c
    c.close()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT customer_id, industry FROM customers",
        "SELECT COUNT(*) FROM customers",
        "SELECT industry, COUNT(*) FROM customers GROUP BY industry",
        "SELECT lower(industry) FROM customers WHERE customer_id < 5",
    ],
)
def test_allowed_reads(conn, sql):
    conn.execute(sql).fetchall()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT company_name FROM customers",  # column not allowed
        "SELECT plan_name FROM plans",  # table not allowed
        "SELECT * FROM customers",  # star pulls in disallowed columns
        "SELECT name FROM sqlite_master",
        "SELECT random()",
        "SELECT sqlite_version()",
        "SELECT hex(industry) FROM customers",
        "SELECT load_extension('x')",
        "PRAGMA table_info(customers)",
        "ATTACH DATABASE ':memory:' AS other",
        "INSERT INTO customers (customer_id) VALUES (1)",
        "UPDATE customers SET industry = 'x'",
        "DELETE FROM customers",
        "DROP TABLE customers",
        "CREATE TABLE extra (a INTEGER)",
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT n FROM r",
    ],
)
def test_everything_else_is_denied(conn, sql):
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute(sql).fetchall()


def test_names_compare_case_insensitively(db_path):
    c = open_readonly(db_path)
    c.set_authorizer(make_authorizer({"Customers"}, {("Customers", "Industry")}))
    c.execute("SELECT INDUSTRY FROM CUSTOMERS").fetchall()
    c.close()
