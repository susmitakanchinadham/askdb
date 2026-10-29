import threading
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from askdb.config import load_semantic_layer
from askdb.executor import (
    PAGE_SIZE,
    ROW_CEILING,
    ExecutionError,
    Executor,
    QueryTimeout,
    TooBusy,
)
from askdb.validator import allowed_from_layer, validate_sql

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data/cloudmetrics.db"


@pytest.fixture(scope="module")
def allowed():
    return allowed_from_layer(load_semantic_layer(ROOT / "config/databases/cloudmetrics.yaml"))


@pytest.fixture
def executor():
    return Executor(DB)


def valid(sql, allowed):
    result = validate_sql(sql, allowed)
    assert result.ok, result.message
    return result


def golden_sql():
    cases = yaml.safe_load((ROOT / "config/validation/cloudmetrics.yaml").read_text("utf-8"))
    out = []
    for case in cases["cases"]:
        if "expected_sql" in case:
            out.append((case["id"], case["expected_sql"]))
        for i, turn in enumerate(case.get("conversation", [])):
            out.append((f"{case['id']}/turn{i}", turn["sql"]))
    return out


@pytest.mark.parametrize(("case_id", "sql"), golden_sql(), ids=[g[0] for g in golden_sql()])
def test_every_golden_query_returns_rows_under_the_authorizer(case_id, sql, allowed, executor):
    page = executor.execute(valid(sql, allowed))
    assert page.columns
    assert page.rows


def test_runaway_query_is_interrupted_at_the_deadline(allowed):
    sql = "SELECT COUNT(*) FROM invoices a, invoices b, invoices c, invoices d"
    with pytest.raises(QueryTimeout):
        Executor(DB, timeout=0.3).execute(valid(sql, allowed))


def test_over_ceiling_result_is_truncated_and_flagged(allowed, executor):
    sql = "SELECT invoice_id FROM invoices"  # 1,831 rows
    first = executor.execute(valid(sql, allowed))
    assert first.truncated and first.total == ROW_CEILING and len(first.rows) == PAGE_SIZE
    last = executor.execute(valid(sql, allowed), page=ROW_CEILING // PAGE_SIZE)
    assert len(last.rows) == PAGE_SIZE and not last.has_more
    beyond = executor.execute(valid(sql, allowed), page=ROW_CEILING // PAGE_SIZE + 1)
    assert beyond.rows == []


def test_small_result_is_not_flagged(allowed, executor):
    page = executor.execute(valid("SELECT plan_name FROM plans", allowed))
    assert not page.truncated and page.total == len(page.rows) == 4


def test_page_two_is_stable_and_disjoint_from_page_one(allowed, executor):
    # status has four values, so ties are everywhere without the tie-breaker.
    sql = "SELECT invoice_id FROM invoices ORDER BY status"
    p1 = executor.execute(valid(sql, allowed), page=1).rows
    p2 = executor.execute(valid(sql, allowed), page=2).rows
    assert len(p2) == PAGE_SIZE
    assert not set(p1) & set(p2)
    assert p2 == executor.execute(valid(sql, allowed), page=2).rows


def test_pages_follow_the_requested_order(allowed, executor):
    sql = "SELECT amount FROM invoices ORDER BY amount DESC"
    rows = []
    for page in (1, 2, 3):
        rows += [r[0] for r in executor.execute(valid(sql, allowed), page=page).rows]
    assert rows == sorted(rows, reverse=True)


def test_existing_limit_is_respected(allowed, executor):
    page = executor.execute(valid("SELECT invoice_id FROM invoices LIMIT 7", allowed))
    assert page.total == 7 and len(page.rows) == 7


def test_concurrency_cap_holds(allowed):
    ex = Executor(DB, timeout=1.5, max_concurrent=1)
    slow = valid("SELECT COUNT(*) FROM invoices a, invoices b, invoices c", allowed)
    quick = valid("SELECT plan_name FROM plans", allowed)
    outcome = []

    def hold():
        try:
            ex.execute(slow)
        except QueryTimeout:
            outcome.append("timeout")

    t = threading.Thread(target=hold)
    t.start()
    refused = False
    for _ in range(200):
        try:
            ex.execute(quick)
        except TooBusy:
            refused = True
            break
        threading.Event().wait(0.01)
    t.join()
    assert refused
    assert outcome == ["timeout"]
    assert ex.execute(quick).total == 4  # the slot was released


def test_unvalidated_statement_is_refused(executor):
    bad = validate_sql("DROP TABLE plans", {"plans": {"plan_name"}})
    with pytest.raises(ValueError):
        executor.execute(bad)


def test_authorizer_cross_check_blocks_a_widened_statement(allowed, executor):
    ok = valid("SELECT plan_name FROM plans", allowed)
    with pytest.raises(ExecutionError):
        executor.execute(replace(ok, sql="SELECT features FROM plans"))
