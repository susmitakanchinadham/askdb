"""End-to-end smoke: the real app (planner, checker, executor, composer, roles, audit, sessions)
with the offline replay model, driven the way the web client drives it.

Also pins the contract between web/src/api.ts and the API routes, so a renamed route or a
changed field name fails here instead of in the browser.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from askdb import strings
from askdb.dev_llm import ReplayLLMClient
from askdb.main import create_app

ROOT = Path(__file__).resolve().parents[2]
USER = {"X-Askdb-User": "smoke", "X-Askdb-Role": "admin"}


@pytest.fixture
def client(config_path, db_path, monkeypatch):
    monkeypatch.setenv("CLOUDMETRICS_DB_PATH", str(db_path))
    replay = ReplayLLMClient(
        ROOT / "config/validation/cloudmetrics.yaml", ROOT / "tests/validation/recorded"
    )
    with TestClient(create_app(config_path, llm=replay), headers=USER) as tc:
        yield tc


def ask(client, question, **kw):
    return client.post("/ask", json={"question": question, **kw}).json()


def test_three_golden_questions_and_a_refusal(client):
    total = ask(client, "What is our current total MRR?")
    assert total["status"] == "answer"
    assert total["columns"] == ["Total MRR"] and total["rows"][0][0].startswith("$")
    assert total["rows"][0][0] in total["sentence"]

    monthly = ask(client, "Show monthly revenue for 2025.", session_id=total["session_id"])
    assert monthly["status"] == "answer" and monthly["total"] == 11
    assert monthly["columns"] == ["Month", "Total Revenue"]
    assert monthly["session_id"] == total["session_id"]

    top = ask(
        client, "Which five industries have the most customers?", session_id=monthly["session_id"]
    )
    assert top["status"] == "answer" and top["total"] == 5

    refused = ask(client, "Mark all overdue invoices as paid.", session_id=top["session_id"])
    assert refused["status"] == "declined"
    assert refused["message"] == strings.MESSAGES["refused"]
    assert "rows" not in refused


def test_questions_the_golden_set_does_not_know_are_declined(client):
    body = ask(client, "What will our revenue be next year?")
    assert body["status"] == "declined"


def test_follow_up_page_feedback_and_new_question(client):
    first = ask(client, "Break down MRR by plan.")
    follow = ask(client, "And split that by industry?", session_id=first["session_id"])
    assert follow["status"] == "answer" and "Industry" in follow["columns"]
    sid = follow["session_id"]

    page = client.post("/ask/page", json={"session_id": sid, "page": 1}).json()
    assert page["status"] == "answer" and page["rows"] == follow["rows"]

    thanks = client.post("/feedback", json={"session_id": sid, "note": "check this"}).json()
    assert thanks == {"status": "ok", "message": strings.FEEDBACK_THANKS}
    (row,) = client.app.state.feedback.rows()
    assert row["question"] == "And split that by industry?" and row["note"] == "check this"

    assert client.post("/session/reset", json={"session_id": sid}).json()["status"] == "ok"
    assert client.post("/ask/page", json={"session_id": sid, "page": 1}).status_code == 409


def test_every_request_was_audited(client):
    ask(client, "What is our current total MRR?")
    ask(client, "Hi, how are you doing today?")
    assert [r["outcome"] for r in client.app.state.audit.rows()] == ["answer", "refuse"]


def test_health_still_answers_without_identity(client):
    body = client.get("/health", headers={"X-Askdb-User": "", "X-Askdb-Role": ""}).json()
    assert body["status"] == "ok"


# ------------------------------------------------------------------ contract with web/


API_TS = (ROOT / "web/src/api.ts").read_text(encoding="utf-8")


def test_every_route_the_web_client_calls_exists(client):
    paths = set(re.findall(r'post\(\s*"(/[a-z/]+)"', API_TS))
    assert paths == {"/ask", "/ask/chip", "/ask/page", "/feedback", "/session/reset"}
    served = set(client.get("/openapi.json").json()["paths"])
    assert paths <= served


def test_answer_fields_match_the_web_types(client):
    body = ask(client, "What is our current total MRR?")
    interface = API_TS.split("export interface AnswerResponse", 1)[1].split("}", 1)[0]
    declared = set(re.findall(r"^\s+([a-z_]+)\??:", interface, re.MULTILINE))
    sent = set(body) - {"status"}
    assert sent <= declared | {"session_id", "notice"}
    assert declared - {"status"} <= sent | {"session_id", "notice"}


def test_web_client_sends_the_field_names_the_api_reads(client):
    for field in ("session_id", "question", "kind", "value", "page", "note"):
        assert re.search(rf"\b{field}\b", API_TS), field
