import pytest

from askdb.executor import ExecutionError, ordered_sql


def _keys(sql: str) -> list[str]:
    """ORDER BY keys with sqlglot's NULLS clauses stripped."""
    tail = sql.split("ORDER BY ", 1)[1]
    return [
        k.strip().replace(" NULLS LAST", "").replace(" NULLS FIRST", "") for k in tail.split(",")
    ]


def test_every_result_column_becomes_a_tie_breaker():
    assert _keys(ordered_sql("SELECT a, b FROM t")) == ["1", "2"]


def test_existing_order_comes_first():
    assert _keys(ordered_sql("SELECT a, b FROM t ORDER BY b DESC")) == ["b DESC", "1", "2"]


def test_union_is_ordered_as_a_whole():
    out = ordered_sql("SELECT a FROM t UNION ALL SELECT a FROM u")
    assert out.startswith("SELECT * FROM (") and "ORDER BY 1" in out


def test_star_is_refused():
    with pytest.raises(ExecutionError):
        ordered_sql("SELECT * FROM t")


def test_statement_with_its_own_limit_is_left_alone():
    sql = "SELECT a, b FROM t ORDER BY b DESC LIMIT 3"
    assert ordered_sql(sql) == sql
