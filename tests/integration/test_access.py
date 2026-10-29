import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from askdb import strings
from askdb.audit import AuditLog
from askdb.config import load_semantic_layer
from askdb.llm import FakeLLMClient, LLMUnavailable
from askdb.main import create_app
from askdb.roles import hidden_terms, load_roles

ROOT = Path(__file__).resolve().parents[2]
LAYER = load_semantic_layer(ROOT / "config/databases/cloudmetrics.yaml")
BOOK = load_roles(ROOT / "config/roles/cloudmetrics.yaml", LAYER)


def headers(role, user="tester"):
    return {"X-Askdb-User": user, "X-Askdb-Role": role}


def reply(**kw):
    return json.dumps(kw)


def plan(sql, interpretation="Test"):
    return reply(type="answer_plan", interpretation=interpretation, sql=sql)


@pytest.fixture
def start(config_path, db_path, audit_path, monkeypatch):
    monkeypatch.setenv("CLOUDMETRICS_DB_PATH", str(db_path))
    opened = []

    def _start(llm, raise_server_exceptions=True):
        client = TestClient(
            create_app(config_path, llm=llm), raise_server_exceptions=raise_server_exceptions
        )
        client.__enter__()
        opened.append(client)
        return client

    yield _start
    for client in opened:
        client.__exit__(None, None, None)


def ask(client, role, question="Anything?", **kw):
    return client.post("/ask", json={"question": question, **kw}, headers=headers(role))


def mentions(text, term):
    return re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE) is not None


# ------------------------------------------------------------ the prompt


@pytest.mark.parametrize("role", ["analyst", "support"])
def test_hidden_names_never_reach_the_model(start, role):
    llm = FakeLLMClient(
        [
            plan("SELECT industry, COUNT(*) AS n FROM customers GROUP BY industry"),
            reply(type="clarify", question="Which period?", options=["2024", "2025"]),
        ]
    )
    client = start(llm)
    ask(client, role, "How many customers by industry?")
    ask(client, role, "Show tickets")
    terms = hidden_terms(LAYER, BOOK.get(role))
    assert terms
    prompts = "\n".join(system + user for system, user in llm.calls)
    assert [t for t in terms if mentions(prompts, t)] == []
    assert "support_tickets" in prompts  # the visible part is still there


def test_admin_prompt_has_everything(start):
    llm = FakeLLMClient([plan("SELECT industry FROM customers")])
    ask(start(llm), "admin")
    assert "account_owner" in llm.calls[0][0] and "CREATE TABLE subscriptions" in llm.calls[0][0]


# ------------------------------------------------------------ statements and results


@pytest.mark.parametrize(
    ("role", "sql", "code"),
    [
        ("analyst", "SELECT account_owner FROM customers", "unknown_column"),
        ("analyst", "SELECT c.account_owner FROM customers c", "unknown_column"),
        ("analyst", "SELECT industry FROM customers ORDER BY account_owner", "unknown_column"),
        ("support", "SELECT SUM(mrr) FROM subscriptions", "table_not_allowed"),
        (
            "support",
            "SELECT c.company_name FROM customers c JOIN invoices i ON i.customer_id = c.customer_id",  # noqa: E501
            "table_not_allowed",
        ),
        ("support", "SELECT account_owner FROM customers", "unknown_column"),
    ],
)
def test_hidden_data_gets_a_neutral_refusal_without_a_retry(start, role, sql, code):
    llm = FakeLLMClient([plan(sql), plan("SELECT industry FROM customers")])
    client = start(llm)
    resp = ask(client, role)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "declined" and body["reason"] == "not_permitted"
    assert body["message"] == strings.MESSAGES["not_permitted"]
    assert "rows" not in body
    assert len(llm.calls) == 1  # the hidden name is never put into a retry prompt
    (row,) = AuditLog(client.app.state.audit.path).rows()
    assert (row["outcome"], row["reason"], row["validator_result"]) == (
        "refuse",
        "not_permitted",
        code,
    )


def test_select_star_only_returns_visible_columns(start):
    client = start(FakeLLMClient([plan("SELECT * FROM customers ORDER BY customer_id LIMIT 3")]))
    body = ask(client, "analyst").json()
    assert body["status"] == "answer"
    assert "Account Owner" not in body["columns"] and "Company Name" in body["columns"]
    assert len(body["rows"]) == 3


def test_admin_can_read_the_account_owner(start):
    client = start(FakeLLMClient([plan("SELECT DISTINCT account_owner FROM customers")]))
    body = ask(client, "admin").json()
    assert body["status"] == "answer" and body["columns"] == ["Account Owner"]


def test_results_never_contain_a_hidden_value(start, db_path):
    conn = sqlite3.connect(db_path)
    owners = {r[0] for r in conn.execute("SELECT DISTINCT account_owner FROM customers")}
    conn.close()
    client = start(FakeLLMClient([plan("SELECT * FROM customers")] * 40))
    for page in range(1, 41):
        body = ask(client, "analyst", page=page).json()
        cells = {c for row in body["rows"] for c in row}
        assert not owners & cells


# ------------------------------------------------------------ identity


@pytest.mark.parametrize(
    "hdrs",
    [{}, {"X-Askdb-User": "u"}, {"X-Askdb-Role": "admin"}, headers("superuser"), headers("")],
)
def test_unknown_or_missing_identity_is_turned_away_and_logged(start, hdrs):
    llm = FakeLLMClient([plan("SELECT industry FROM customers")])
    client = start(llm)
    resp = client.post("/ask", json={"question": "x"}, headers=hdrs)
    assert resp.status_code == 403
    assert resp.json() == {"status": "error", "message": strings.MESSAGES["no_access"]}
    assert llm.calls == []
    (row,) = AuditLog(client.app.state.audit.path).rows()
    assert (row["outcome"], row["reason"]) == ("error", "no_access")


# ------------------------------------------------------------ audit


def test_audit_rows_for_answer_clarify_refuse_and_error(start, db_path):
    class Down:
        def complete(self, system, user):
            raise LLMUnavailable("down")

    replies = [
        plan("SELECT company_name FROM customers ORDER BY customer_id LIMIT 5", "Five customers"),
        reply(type="clarify", question="Which period?", options=["2024", "2025"]),
        reply(type="refuse", reason="data_change"),
        plan("SELECT 1 FROM nowhere"),
        plan("SELECT 1 FROM nowhere"),
    ]
    client = start(FakeLLMClient(replies))
    ask(client, "analyst", "List five customers")
    ask(client, "analyst", "Revenue?")
    ask(client, "support", "Mark invoices paid")
    ask(client, "analyst", "Broken")
    client.app.state.planners["analyst"].client = Down()
    ask(client, "analyst", "Anything")

    rows = AuditLog(client.app.state.audit.path).rows()
    assert [r["outcome"] for r in rows] == ["answer", "clarify", "refuse", "refuse", "error"]

    answer, clarify, refuse, failed, error = rows
    assert (answer["user"], answer["role"], answer["question"]) == (
        "tester",
        "analyst",
        "List five customers",
    )
    assert answer["plan"] == "Five customers"
    assert answer["row_count"] == 5 and answer["validator_result"] == "ok"
    assert answer["sql"].startswith("SELECT") and answer["glossary_version"] == LAYER.version
    assert answer["duration_ms"] >= 0 and answer["ts"]
    assert clarify["plan"] == "Which period?" and clarify["row_count"] is None
    assert (refuse["reason"], refuse["role"]) == ("data_change", "support")
    assert (failed["reason"], failed["validator_result"]) == (
        "validation_failed",
        "table_not_allowed",
    )
    assert error["reason"] == "llm_unavailable"


def test_audit_never_holds_result_values(start, db_path):
    conn = sqlite3.connect(db_path)
    names = [
        r[0]
        for r in conn.execute("SELECT company_name FROM customers ORDER BY customer_id LIMIT 30")
    ]
    conn.close()
    client = start(
        FakeLLMClient([plan("SELECT company_name FROM customers ORDER BY customer_id LIMIT 30")])
    )
    body = ask(client, "analyst", "List some customers").json()
    assert len(body["rows"]) == 25 and {r[0] for r in body["rows"]} <= set(names)
    dump = json.dumps([dict(r) for r in AuditLog(client.app.state.audit.path).rows()])
    assert [n for n in names if n in dump] == []


def test_a_failed_audit_write_fails_the_request_and_returns_no_data(start):
    client = start(
        FakeLLMClient([plan("SELECT company_name FROM customers LIMIT 3")]),
        raise_server_exceptions=False,
    )

    class Broken:
        def write(self, record):
            raise sqlite3.OperationalError("disk full")

    client.app.state.audit = Broken()
    resp = ask(client, "admin")
    assert resp.status_code == 500
    assert resp.json() == {"status": "error", "message": strings.MESSAGES["unexpected"]}
