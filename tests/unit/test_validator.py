from pathlib import Path

import pytest
import yaml

from askdb.config import load_semantic_layer
from askdb.db import open_readonly
from askdb.validator import Code, allowed_from_layer, validate_sql

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def allowed():
    return allowed_from_layer(load_semantic_layer(ROOT / "config/databases/cloudmetrics.yaml"))


@pytest.fixture(scope="module")
def conn():
    c = open_readonly(ROOT / "data/cloudmetrics.db")
    yield c
    c.close()


def golden_sql() -> list[tuple[str, str]]:
    cases = yaml.safe_load((ROOT / "config/validation/cloudmetrics.yaml").read_text("utf-8"))
    out = []
    for case in cases["cases"]:
        if "expected_sql" in case:
            out.append((case["id"], case["expected_sql"]))
        for i, turn in enumerate(case.get("conversation", [])):
            out.append((f"{case['id']}/turn{i}", turn["sql"]))
    return out


def rows(conn, sql):
    return sorted(repr(r) for r in conn.execute(sql).fetchall())


# ---------------------------------------------------------------- golden set


@pytest.mark.parametrize(("case_id", "sql"), golden_sql(), ids=[g[0] for g in golden_sql()])
def test_golden_sql_passes_and_rewrite_returns_same_rows(case_id, sql, allowed, conn):
    result = validate_sql(sql, allowed)
    assert result.ok, result.message
    assert rows(conn, result.sql) == rows(conn, sql)


def test_golden_set_has_the_expected_number_of_queries():
    assert len(golden_sql()) == 25


# ------------------------------------------------------------ what is accepted


def test_result_reports_tables_and_columns(allowed):
    r = validate_sql(
        "SELECT c.company_name, SUM(i.amount) FROM invoices i "
        "JOIN customers c ON c.customer_id = i.customer_id GROUP BY c.company_name",
        allowed,
    )
    assert r.ok
    assert r.tables == {"invoices", "customers"}
    assert r.columns == {
        ("customers", "company_name"),
        ("customers", "customer_id"),
        ("invoices", "amount"),
        ("invoices", "customer_id"),
    }


def test_trailing_semicolon_and_case_are_fine(allowed):
    assert validate_sql("select PLAN_NAME from PLANS;", allowed).ok


def test_union_cte_and_subquery_are_fine(allowed):
    union = "SELECT plan_name FROM plans UNION SELECT industry FROM customers"
    assert validate_sql(union, allowed).ok
    assert validate_sql(
        "WITH t AS (SELECT customer_id, COUNT(*) n FROM support_tickets GROUP BY customer_id) "
        "SELECT * FROM t WHERE n > (SELECT AVG(n) FROM t)",
        allowed,
    ).ok


def test_comments_are_not_carried_into_the_rewrite(allowed):
    r = validate_sql("SELECT 1 /* DROP TABLE plans */ -- more", allowed)
    assert r.ok
    assert "DROP" not in r.sql
    assert "/*" not in r.sql
    assert "--" not in r.sql


def test_crafted_comment_cannot_break_out_of_the_rewrite(allowed, conn):
    sql = "SELECT plan_name FROM plans /* a */ -- */ ; DROP TABLE plans\n"
    r = validate_sql(sql, allowed)
    assert r.ok
    assert "DROP" not in r.sql
    assert ";" not in r.sql
    assert len(conn.execute(r.sql).fetchall()) == 4


def test_rewrite_is_itself_valid(allowed):
    r = validate_sql("SELECT * FROM plans", allowed)
    again = validate_sql(r.sql, allowed)
    assert again.ok
    assert again.sql == r.sql


@pytest.mark.parametrize(
    "expr",
    [
        "date('2025-01-15', 'start of month')",
        "date('2025-01-15', '-1 month')",
        "datetime('2025-01-15 10:00:00')",
        "time('2025-01-15 10:00:00')",
        "julianday('2025-01-15') - julianday('2025-01-01')",
        "strftime('%Y-%m', '2025-01-15')",
        "CAST(strftime('%m', '2025-07-15') AS INTEGER)",
        "round(2.567, 2)",
        "coalesce(NULL, 1)",
        "ifnull(NULL, 2)",
        "nullif(1, 1)",
        "iif(1 > 0, 'a', 'b')",
        "instr('hello', 'l')",
        "substr('hello', 2, 3)",
        "replace('hello', 'l', 'L')",
        "trim('  x  ')",
        "ltrim('  x  ')",
        "rtrim('  x  ')",
        "lower('AbC') || upper('AbC')",
        "length('abc')",
        "abs(-3)",
        "sign(-3)",
        "max(1, 5, 3)",
        "min(4, 2, 9)",
        "total(1)",
        "group_concat(1)",
        "CASE WHEN 1 > 0 THEN 'y' ELSE 'n' END",
        "1 BETWEEN 0 AND 2",
        "'abc' LIKE 'a%'",
        "'abc' IN ('abc', 'd')",
    ],
)
def test_allowed_functions_keep_their_meaning_after_rewrite(expr, allowed, conn):
    sql = f"SELECT {expr} AS v"
    r = validate_sql(sql, allowed)
    assert r.ok, r.message
    assert conn.execute(r.sql).fetchall() == conn.execute(sql).fetchall()


def test_window_functions_are_allowed(allowed, conn):
    sql = "SELECT plan_name, row_number() OVER (ORDER BY monthly_price DESC) AS rk FROM plans"
    r = validate_sql(sql, allowed)
    assert r.ok, r.message
    assert rows(conn, r.sql) == rows(conn, sql)


# --------------------------------------------------------- injection corpus

CORPUS = [
    # stacked statements
    ("SELECT 1; DROP TABLE plans", Code.MULTIPLE_STATEMENTS),
    ("SELECT 1; SELECT 2", Code.MULTIPLE_STATEMENTS),
    ("SELECT 1 /* x */; DROP TABLE plans", Code.MULTIPLE_STATEMENTS),
    ("SELECT 1;; DELETE FROM plans", Code.MULTIPLE_STATEMENTS),
    # not a SELECT
    ("DROP TABLE plans", Code.NOT_SELECT),
    ("DELETE FROM plans", Code.NOT_SELECT),
    ("INSERT INTO plans (plan_name) VALUES ('x')", Code.NOT_SELECT),
    ("UPDATE plans SET plan_name = 'x'", Code.NOT_SELECT),
    ("CREATE TABLE t (a INTEGER)", Code.NOT_SELECT),
    ("ALTER TABLE plans ADD COLUMN x TEXT", Code.NOT_SELECT),
    ("PRAGMA table_info(plans)", Code.NOT_SELECT),
    ("PRAGMA query_only = OFF", Code.NOT_SELECT),
    ("ATTACH DATABASE 'other.db' AS other", Code.NOT_SELECT),
    ("DETACH DATABASE other", Code.NOT_SELECT),
    ("EXPLAIN SELECT 1", Code.NOT_SELECT),
    ("VACUUM", Code.NOT_SELECT),
    ("BEGIN", Code.NOT_SELECT),
    ("VALUES (1)", Code.NOT_SELECT),
    ("WITH x AS (SELECT 1) DELETE FROM plans", Code.NOT_SELECT),
    ("WITH x AS (SELECT 1) INSERT INTO plans SELECT * FROM plans", Code.NOT_SELECT),
    # forbidden functions, plain and hidden in subqueries
    ("SELECT load_extension('evil')", Code.FUNCTION_NOT_ALLOWED),
    ("SELECT readfile('/etc/passwd')", Code.FUNCTION_NOT_ALLOWED),
    ("SELECT writefile('x', 'y')", Code.FUNCTION_NOT_ALLOWED),
    ("SELECT edit('x')", Code.FUNCTION_NOT_ALLOWED),
    ("SELECT fts3_tokenizer('x')", Code.FUNCTION_NOT_ALLOWED),
    ("SELECT zeroblob(1000000000)", Code.FUNCTION_NOT_ALLOWED),
    ("SELECT randomblob(1000000000)", Code.FUNCTION_NOT_ALLOWED),
    ("SELECT printf('%1000000d', 1)", Code.FUNCTION_NOT_ALLOWED),
    ("SELECT random()", Code.FUNCTION_NOT_ALLOWED),
    ("SELECT sqlite_version()", Code.FUNCTION_NOT_ALLOWED),
    ("SELECT hex(plan_name) FROM plans", Code.FUNCTION_NOT_ALLOWED),
    ("SELECT json_extract(features, '$.a') FROM plans", Code.FUNCTION_NOT_ALLOWED),
    (
        "SELECT plan_name FROM plans WHERE plan_id IN (SELECT length(readfile('x')))",
        Code.FUNCTION_NOT_ALLOWED,
    ),
    ("SELECT plan_name FROM plans ORDER BY load_extension('x')", Code.FUNCTION_NOT_ALLOWED),
    ("WITH t AS (SELECT writefile('a', 'b') AS w) SELECT w FROM t", Code.FUNCTION_NOT_ALLOWED),
    # table-valued functions
    ("SELECT * FROM pragma_table_info('plans')", Code.TABLE_FUNCTION),
    ("SELECT * FROM json_each('[1,2]')", Code.TABLE_FUNCTION),
    ("SELECT * FROM generate_series(1, 1000000000)", Code.TABLE_FUNCTION),
    # system tables, other databases, unknown tables
    ("SELECT * FROM sqlite_master", Code.TABLE_NOT_ALLOWED),
    ("SELECT * FROM sqlite_schema", Code.TABLE_NOT_ALLOWED),
    ("SELECT * FROM sqlite_temp_master", Code.TABLE_NOT_ALLOWED),
    ("SELECT * FROM users", Code.TABLE_NOT_ALLOWED),
    ("SELECT * FROM main.plans", Code.QUALIFIED_TABLE),
    ("SELECT * FROM temp.plans", Code.QUALIFIED_TABLE),
    ("SELECT * FROM other.plans", Code.QUALIFIED_TABLE),
    ("SELECT (SELECT sql FROM sqlite_master LIMIT 1)", Code.TABLE_NOT_ALLOWED),
    ("SELECT plan_name FROM plans UNION SELECT name FROM sqlite_master", Code.TABLE_NOT_ALLOWED),
    ("SELECT * FROM plans p JOIN secrets s ON s.id = p.plan_id", Code.TABLE_NOT_ALLOWED),
    ("SELECT * FROM plans WHERE plan_id IN (SELECT id FROM secrets)", Code.TABLE_NOT_ALLOWED),
    ("SELECT * FROM plans WHERE EXISTS (SELECT 1 FROM sqlite_master)", Code.TABLE_NOT_ALLOWED),
    # a CTE named after a real table must not hide what it reads
    (
        "WITH sqlite_master AS (SELECT * FROM sqlite_master) SELECT * FROM sqlite_master",
        Code.TABLE_NOT_ALLOWED,
    ),
    (
        "WITH secrets AS (SELECT * FROM secrets) SELECT * FROM secrets",
        Code.TABLE_NOT_ALLOWED,
    ),
    # recursion
    (
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT n FROM r",
        Code.RECURSIVE_CTE,
    ),
    (
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 5) "
        "SELECT n FROM r",
        Code.RECURSIVE_CTE,
    ),
    # placeholders
    ("SELECT ?", Code.PLACEHOLDER),
    ("SELECT :name", Code.PLACEHOLDER),
    ("SELECT plan_name FROM plans WHERE plan_id = @id", Code.PLACEHOLDER),
    # unknown columns
    ("SELECT nope FROM plans", Code.UNKNOWN_COLUMN),
    ("SELECT p.nope FROM plans p", Code.UNKNOWN_COLUMN),
    ('SELECT "paid" FROM invoices', Code.UNKNOWN_COLUMN),
    # empty and broken
    ("", Code.EMPTY),
    ("   \n", Code.EMPTY),
    (";", Code.EMPTY),
    ("SELECT", Code.PARSE_ERROR),
    ("SELEC * FROM plans", Code.PARSE_ERROR),
    ("SELECT * FROM", Code.PARSE_ERROR),
    ("SELECT 'unterminated", Code.PARSE_ERROR),
]


@pytest.mark.parametrize(("sql", "code"), CORPUS, ids=[c[0][:70] for c in CORPUS])
def test_corpus_is_rejected_with_the_right_code(sql, code, allowed):
    r = validate_sql(sql, allowed)
    assert r.code == code, (r.code, r.message)
    assert r.sql == ""
    assert not r.ok


def test_deeply_nested_input_is_rejected_not_crashed(allowed):
    r = validate_sql("SELECT " + "(" * 5000 + "1" + ")" * 5000, allowed)
    assert not r.ok


def test_error_messages_are_plain_and_never_echo_sql_bodies(allowed):
    r = validate_sql("SELECT * FROM secrets", allowed)
    assert r.message == "Table 'secrets' is not available."


# ------------------------------------------------------ disallowed columns


@pytest.fixture
def restricted(allowed):
    a = {t: set(cols) for t, cols in allowed.items()}
    a["customers"].discard("company_name")
    a["customers"].discard("account_owner")
    return a


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT company_name FROM customers",
        "SELECT c.company_name FROM customers c",
        "SELECT industry FROM customers WHERE company_name = 'x'",
        "SELECT industry, COUNT(*) FROM customers GROUP BY industry ORDER BY company_name",
        "SELECT i.amount FROM invoices i JOIN customers c ON c.company_name = 'x'",
        "SELECT * FROM (SELECT account_owner FROM customers) t",
        "WITH t AS (SELECT company_name FROM customers) SELECT * FROM t",
        "SELECT (SELECT MAX(account_owner) FROM customers)",
        "SELECT industry FROM customers UNION SELECT company_name FROM customers",
        "SELECT COUNT(company_name) FROM customers",
        "SELECT industry FROM customers GROUP BY industry HAVING MAX(account_owner) > 'a'",
        "SELECT industry FROM customers GROUP BY account_owner",
        "SELECT industry, row_number() OVER (PARTITION BY account_owner ORDER BY company_name) "
        "FROM customers",
        "SELECT SUM(customer_id) FILTER (WHERE account_owner = 'x') FROM customers",
        "SELECT industry FROM customers c JOIN plans p USING (account_owner)",
        "SELECT t.company_name FROM (SELECT industry FROM customers) t",
        # a result alias that shadows a hidden column must not turn ORDER BY into a way to read it
        "SELECT industry AS account_owner FROM customers ORDER BY account_owner || 'x'",
    ],
)
def test_hidden_columns_are_rejected_wherever_they_appear(sql, restricted):
    r = validate_sql(sql, restricted)
    assert r.code == Code.UNKNOWN_COLUMN, (r.code, r.message)


def test_star_only_expands_to_visible_columns(restricted):
    r = validate_sql("SELECT * FROM customers", restricted)
    assert r.ok
    assert "company_name" not in r.sql
    assert "account_owner" not in r.sql
    assert r.columns == {
        ("customers", "customer_id"),
        ("customers", "industry"),
        ("customers", "company_size"),
        ("customers", "country"),
        ("customers", "signup_date"),
    }


def test_star_through_a_subquery_cannot_reach_hidden_columns(restricted):
    r = validate_sql("SELECT * FROM (SELECT * FROM customers) t", restricted)
    assert r.ok
    assert "company_name" not in r.sql


def test_count_star_is_untouched_by_restriction(restricted, conn):
    r = validate_sql("SELECT COUNT(*) FROM customers", restricted)
    assert r.ok
    assert conn.execute(r.sql).fetchone() == (200,)


def test_hidden_table_is_unavailable(allowed):
    a = {t: c for t, c in allowed.items() if t != "support_tickets"}
    assert validate_sql("SELECT COUNT(*) FROM support_tickets", a).code == Code.TABLE_NOT_ALLOWED


def test_alias_that_shadows_a_hidden_column_is_read_as_the_alias(restricted):
    r = validate_sql(
        "SELECT industry AS account_owner FROM customers "
        "GROUP BY 1 HAVING MAX(account_owner) > 'a'",
        restricted,
    )
    assert r.ok
    assert r.columns == {("customers", "industry")}


def test_ordering_by_a_result_name_still_works(restricted, conn):
    sql = (
        "SELECT industry, COUNT(*) AS n FROM customers "
        "GROUP BY industry HAVING n > 1 ORDER BY n DESC"
    )
    r = validate_sql(sql, restricted)
    assert r.ok, r.message
    assert conn.execute(r.sql).fetchall() == conn.execute(sql).fetchall()


def test_having_on_a_visible_column_works(allowed, conn):
    sql = (
        "SELECT customer_id, SUM(amount) FROM invoices "
        "GROUP BY customer_id HAVING SUM(amount) > 5000"
    )
    r = validate_sql(sql, allowed)
    assert r.ok, r.message
    assert rows(conn, r.sql) == rows(conn, sql)
