import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from askdb.llm import FakeLLMClient, LLMUnavailable
from askdb.main import create_app

ADMIN = {"X-Askdb-User": "tester", "X-Askdb-Role": "admin"}


def reply(**kw):
    return json.dumps(kw)


@pytest.fixture
def make_client(config_path, db_path, monkeypatch):
    monkeypatch.setenv("CLOUDMETRICS_DB_PATH", str(db_path))
    clients = []

    def make(llm):
        tc = TestClient(create_app(config_path, llm=llm), headers=ADMIN)
        tc.__enter__()
        clients.append(tc)
        return tc

    yield make
    for tc in clients:
        tc.__exit__(None, None, None)


def test_answer_returns_interpretation_and_table_without_the_statement(make_client):
    llm = FakeLLMClient(
        [
            reply(
                type="answer_plan",
                interpretation="Active MRR by plan",
                sql="SELECT p.plan_name, SUM(s.mrr) AS total_mrr FROM subscriptions s "
                "JOIN plans p ON p.plan_id = s.plan_id WHERE s.status = 'active' "
                "GROUP BY p.plan_name ORDER BY total_mrr DESC",
            )
        ]
    )
    body = make_client(llm).post("/ask", json={"question": "MRR by plan?"}).json()
    assert body["status"] == "answer"
    assert body["interpretation"] == "Active MRR by plan"
    assert body["columns"] == ["Plan Name", "Total MRR"]
    assert body["sentence"] == "Found 4 results for Active MRR by plan."
    assert body["rows"][0][1].startswith("$")
    assert body["total"] == len(body["rows"]) == 4
    assert not body["truncated"]
    assert "select" not in json.dumps(body).lower()


def test_clarify_and_decline_and_outage(make_client):
    clarify = make_client(
        FakeLLMClient([reply(type="clarify", question="Which period?", options=["2024", "2025"])])
    )
    assert clarify.post("/ask", json={"question": "Revenue?"}).json() == {
        "status": "clarify",
        "question": "Which period?",
        "options": ["2024", "2025"],
    }

    declined = make_client(FakeLLMClient([reply(type="refuse", reason="data_change")]))
    body = declined.post("/ask", json={"question": "Mark invoices paid"}).json()
    assert body["status"] == "declined"
    assert body["message"] == "I can't answer that reliably yet."

    class Down:
        def complete(self, system, user):
            raise LLMUnavailable("down")

    body = make_client(Down()).post("/ask", json={"question": "MRR?"}).json()
    assert body == {
        "status": "unavailable",
        "message": "I can't answer right now, please try again shortly.",
    }


def test_double_validation_failure_is_declined_politely(make_client):
    bad = reply(type="answer_plan", interpretation="x", sql="DELETE FROM plans")
    body = make_client(FakeLLMClient([bad, bad])).post("/ask", json={"question": "x"}).json()
    assert body["status"] == "declined"
    banned = ("sql", "query", "table", "column", "timeout", "schema")
    assert not any(w in body["message"].lower() for w in banned)


def test_empty_question_is_rejected(make_client):
    assert make_client(FakeLLMClient([])).post("/ask", json={"question": ""}).status_code == 422


def test_no_result_rows_ever_reach_the_llm(make_client, db_path):
    sql = "SELECT company_name, account_owner FROM customers ORDER BY customer_id"
    llm = FakeLLMClient([reply(type="answer_plan", interpretation="Customers", sql=sql)] * 2)
    client = make_client(llm)
    first = client.post("/ask", json={"question": "List customers"}).json()
    assert first["status"] == "answer" and first["rows"]
    client.post("/ask", json={"question": "List customers again"})

    conn = sqlite3.connect(db_path)
    values = {
        str(v)
        for row in conn.execute("SELECT company_name, account_owner FROM customers")
        for v in row
    }
    conn.close()
    prompts = "\n".join(system + user for system, user in llm.calls)
    leaked = [v for v in values if v in prompts]
    assert not leaked, leaked[:5]


def test_without_a_key_ask_degrades_to_the_outage_message(make_client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from askdb.llm import AnthropicClient

    body = make_client(AnthropicClient()).post("/ask", json={"question": "MRR?"}).json()
    assert body["status"] == "unavailable"
