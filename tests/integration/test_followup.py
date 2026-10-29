import json
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from askdb import strings
from askdb.feedback import FeedbackLog
from askdb.llm import FakeLLMClient
from askdb.main import create_app
from askdb.session import SessionStore, UserLimiter

MRR = "SELECT SUM(mrr) AS total_mrr FROM subscriptions WHERE status = 'active'"
BY_PLAN = (
    "SELECT p.plan_name, SUM(s.mrr) AS total_mrr FROM subscriptions s "
    "JOIN plans p ON p.plan_id = s.plan_id WHERE s.status = 'active' GROUP BY p.plan_name"
)


def hdr(role="admin", user="tester"):
    return {"X-Askdb-User": user, "X-Askdb-Role": role}


def plan(sql, interpretation="Active MRR", chips=None):
    body = {"type": "answer_plan", "interpretation": interpretation, "sql": sql}
    if chips is not None:
        body["chips"] = chips
    return json.dumps(body)


@pytest.fixture
def start(config_path, db_path, monkeypatch):
    monkeypatch.setenv("CLOUDMETRICS_DB_PATH", str(db_path))
    opened = []

    def _start(llm):
        client = TestClient(create_app(config_path, llm=llm))
        client.__enter__()
        client.app.state.limiter = UserLimiter(per_minute=1000)
        opened.append(client)
        return client

    yield _start
    for client in opened:
        client.__exit__(None, None, None)


def ask(client, question="q", role="admin", user="tester", **kw):
    return client.post("/ask", json={"question": question, **kw}, headers=hdr(role, user))


# ------------------------------------------------------------------ follow-ups


def test_follow_up_carries_the_earlier_reading_to_the_model(start):
    llm = FakeLLMClient([plan(MRR, "Active MRR"), plan(BY_PLAN, "Active MRR by plan")])
    client = start(llm)
    first = ask(client, "What is MRR?").json()
    second = ask(client, "And by plan?", session_id=first["session_id"]).json()
    assert second["status"] == "answer" and second["session_id"] == first["session_id"]
    assert "Earlier in this conversation" not in llm.calls[0][1]
    assert "- Active MRR" in llm.calls[1][1] and "And by plan?" in llm.calls[1][1]


def test_prior_readings_hold_text_only_never_rows(start, db_path):
    conn = sqlite3.connect(db_path)
    names = [r[0] for r in conn.execute("SELECT company_name FROM customers LIMIT 40")]
    conn.close()
    llm = FakeLLMClient(
        [plan("SELECT company_name FROM customers ORDER BY customer_id LIMIT 40"), plan(MRR)]
    )
    client = start(llm)
    first = ask(client, "List customers").json()
    ask(client, "And MRR?", session_id=first["session_id"])
    prompts = "\n".join(system + user for system, user in llm.calls)
    assert [n for n in names if n in prompts] == []


def test_a_clarifying_turn_is_remembered(start):
    clarify = json.dumps({"type": "clarify", "question": "Which month?", "options": ["Oct", "Nov"]})
    llm = FakeLLMClient([clarify, plan(MRR)])
    client = start(llm)
    first = ask(client, "How much revenue last month?").json()
    ask(client, "November", session_id=first["session_id"])
    assert "How much revenue last month?" in llm.calls[1][1]
    assert "Which month?" in llm.calls[1][1]


def test_session_expires_after_thirty_minutes_and_the_user_is_told(start):
    llm = FakeLLMClient([plan(MRR), plan(MRR)])
    client = start(llm)
    now = [0.0]
    client.app.state.sessions = SessionStore(clock=lambda: now[0])
    first = ask(client, "What is MRR?").json()
    assert "notice" not in first
    now[0] += 30 * 60 + 1
    second = ask(client, "And again?", session_id=first["session_id"]).json()
    assert second["session_id"] != first["session_id"]
    assert second["notice"] == strings.SESSION_ENDED
    assert "Earlier in this conversation" not in llm.calls[1][1]


def test_new_question_clears_the_context(start):
    llm = FakeLLMClient([plan(MRR), plan(MRR)])
    client = start(llm)
    first = ask(client, "What is MRR?").json()
    reset = client.post("/session/reset", json={"session_id": first["session_id"]}, headers=hdr())
    assert reset.json() == {"status": "ok", "message": strings.NEW_QUESTION}
    second = ask(client, "Something else", session_id=first["session_id"]).json()
    assert second["session_id"] == first["session_id"] and "notice" not in second
    assert "Earlier in this conversation" not in llm.calls[1][1]


def test_reset_needs_an_identity(start):
    client = start(FakeLLMClient([]))
    assert client.post("/session/reset", json={"session_id": "x"}).status_code == 403


def test_someone_elses_session_id_brings_no_context(start):
    llm = FakeLLMClient(
        [
            plan(
                "SELECT account_owner, COUNT(*) FROM customers GROUP BY account_owner",
                "By account owner",
            ),
            plan(MRR),
        ]
    )
    client = start(llm)
    admin = ask(client, "Customers per owner", role="admin").json()
    other = ask(
        client, "And MRR?", role="analyst", user="analyst1", session_id=admin["session_id"]
    ).json()
    assert other["session_id"] != admin["session_id"] and other["notice"]
    assert "account owner" not in llm.calls[1][1].lower()


def test_follow_up_under_a_restricted_role_cannot_reach_hidden_columns(start):
    llm = FakeLLMClient(
        [
            plan(
                "SELECT industry, COUNT(*) AS n FROM customers GROUP BY industry",
                "Customers by industry",
            ),
            plan("SELECT account_owner, COUNT(*) AS n FROM customers GROUP BY account_owner"),
            plan("SELECT industry FROM customers"),
        ]
    )
    client = start(llm)
    first = ask(client, "Customers by industry", role="analyst").json()
    follow = ask(
        client, "And by account owner?", role="analyst", session_id=first["session_id"]
    ).json()
    assert follow["status"] == "declined" and follow["reason"] == "not_permitted"
    assert follow["message"] == strings.MESSAGES["not_permitted"]
    assert "rows" not in follow
    assert len(llm.calls) == 2  # refused at once, no retry that would repeat the hidden name


# ------------------------------------------------------------------ chips

CHIPS = [
    {"kind": "grouping", "options": ["by plan", "by industry"]},
    {"kind": "period", "options": ["2024", "2025"]},
]


def test_answer_offers_the_chips_the_model_proposed(start):
    client = start(FakeLLMClient([plan(MRR, chips=CHIPS)]))
    body = ask(client).json()
    assert body["chips"] == CHIPS


def test_malformed_chips_are_dropped_not_fatal(start):
    bad = [
        {"kind": "colour", "options": ["red", "blue"]},
        {"kind": "period", "options": ["only one"]},
        {"kind": "grouping"},
        "text",
        {"kind": "measure", "options": ["a", "b", "c", "d", "e"]},
        {"kind": "measure", "options": ["dup", "dup"]},
    ]
    body = ask(start(FakeLLMClient([plan(MRR, chips=bad)]))).json()
    assert body["chips"] == [{"kind": "measure", "options": ["a", "b", "c", "d"]}]


def test_chips_with_banned_wording_are_not_shown(start):
    chips = [{"kind": "grouping", "options": ["by table", "by plan"]}]
    assert ask(start(FakeLLMClient([plan(MRR, chips=chips)]))).json()["chips"] == []


def test_chip_change_reruns_through_the_planner_and_the_checker(start):
    llm = FakeLLMClient(
        [plan(MRR, "Active MRR", chips=CHIPS), plan(BY_PLAN, "Active MRR by plan", chips=CHIPS)]
    )
    client = start(llm)
    first = ask(client, "What is MRR?").json()
    resp = client.post(
        "/ask/chip",
        json={"session_id": first["session_id"], "kind": "grouping", "value": "by plan"},
        headers=hdr(),
    ).json()
    assert resp["status"] == "answer" and resp["interpretation"] == "Active MRR by plan"
    assert resp["total"] == 4
    system, user = llm.calls[1]
    assert "change the grouping to: by plan" in user and "- Active MRR" in user
    rows = client.app.state.audit.rows()
    assert [r["outcome"] for r in rows] == ["answer", "answer"]
    assert "by plan" in rows[1]["question"]


def test_chip_rerun_goes_through_the_same_validator(start):
    llm = FakeLLMClient(
        [plan(MRR, chips=CHIPS), plan("DROP TABLE plans"), plan("DROP TABLE plans")]
    )
    client = start(llm)
    first = ask(client).json()
    resp = client.post(
        "/ask/chip",
        json={"session_id": first["session_id"], "kind": "period", "value": "2024"},
        headers=hdr(),
    ).json()
    assert resp["status"] == "declined" and resp["reason"] == "validation_failed"
    assert "rows" not in resp


@pytest.mark.parametrize(
    ("kind", "value"),
    [("period", "1999"), ("colour", "red"), ("grouping", "by account owner"), ("period", "")],
)
def test_chip_that_was_not_offered_is_refused_without_calling_the_model(start, kind, value):
    llm = FakeLLMClient([plan(MRR, chips=CHIPS)])
    client = start(llm)
    first = ask(client).json()
    resp = client.post(
        "/ask/chip",
        json={"session_id": first["session_id"], "kind": kind, "value": value or "x"},
        headers=hdr(),
    )
    assert resp.status_code in (400, 422)
    assert resp.json()["message"] in (strings.MESSAGES["no_chip"], strings.MESSAGES["invalid"])
    assert len(llm.calls) == 1


def test_chip_without_any_earlier_answer_is_refused(start):
    llm = FakeLLMClient([])
    client = start(llm)
    resp = client.post(
        "/ask/chip", json={"session_id": "nope", "kind": "period", "value": "2024"}, headers=hdr()
    )
    assert resp.status_code == 400 and llm.calls == []


# ------------------------------------------------------------------ feedback


def test_looks_wrong_stores_what_an_admin_needs(start, db_path):
    client = start(FakeLLMClient([plan(BY_PLAN, "Active MRR by plan")]))
    first = ask(client, "MRR by plan?").json()
    resp = client.post(
        "/feedback", json={"session_id": first["session_id"], "note": " too low "}, headers=hdr()
    )
    assert resp.json() == {"status": "ok", "message": strings.FEEDBACK_THANKS}
    (row,) = FeedbackLog(client.app.state.feedback.path).rows()
    assert row["question"] == "MRR by plan?"
    assert row["plan"] == "Active MRR by plan"
    assert row["sql"].startswith("SELECT") and "plans" in row["sql"]
    assert row["glossary_version"] == client.app.state.layer.version
    assert row["row_count"] == 4
    assert (row["user"], row["role"], row["note"]) == ("tester", "admin", "too low")
    assert row["ts"]
    assert "Starter" not in json.dumps(dict(row))  # a value from the results


def test_looks_wrong_reports_the_latest_answer(start):
    client = start(FakeLLMClient([plan(MRR, "First"), plan(BY_PLAN, "Second")]))
    first = ask(client, "one").json()
    ask(client, "two", session_id=first["session_id"])
    client.post("/feedback", json={"session_id": first["session_id"]}, headers=hdr())
    (row,) = client.app.state.feedback.rows()
    assert (row["question"], row["plan"], row["note"]) == ("two", "Second", None)


def test_looks_wrong_without_an_answer_is_a_clean_conflict(start):
    client = start(FakeLLMClient([]))
    resp = client.post("/feedback", json={"session_id": "none"}, headers=hdr())
    assert resp.status_code == 409
    assert resp.json()["message"] == strings.MESSAGES["no_answer"]
    assert client.app.state.feedback.rows() == []


def test_looks_wrong_cannot_use_someone_elses_session(start):
    client = start(FakeLLMClient([plan(MRR)]))
    first = ask(client, user="alice").json()
    resp = client.post(
        "/feedback", json={"session_id": first["session_id"]}, headers=hdr(user="bob")
    )
    assert resp.status_code == 409
    assert client.app.state.feedback.rows() == []


def test_looks_wrong_needs_an_identity(start):
    client = start(FakeLLMClient([]))
    assert client.post("/feedback", json={"session_id": "x"}).status_code == 403


# ------------------------------------------------------------------ limits


def test_per_user_rate_limit_answers_politely_and_is_audited(start):
    llm = FakeLLMClient([plan(MRR)] * 3)
    client = start(llm)
    client.app.state.limiter = UserLimiter(per_minute=2)
    assert ask(client).status_code == 200
    assert ask(client).status_code == 200
    blocked = ask(client)
    assert blocked.status_code == 429
    assert blocked.json()["message"] == strings.MESSAGES["rate_limited"]
    assert len(llm.calls) == 2  # the model was not called for the blocked request
    assert ask(client, user="someone-else").status_code == 200
    rows = client.app.state.audit.rows()
    assert [(r["outcome"], r["reason"]) for r in rows][2] == ("error", "rate_limited")


def test_one_question_at_a_time_per_user(start):
    entered, release = threading.Event(), threading.Event()

    def slow(system, user):
        entered.set()
        release.wait(5)
        return plan(MRR)

    client = start(FakeLLMClient(slow))
    first = {}
    worker = threading.Thread(target=lambda: first.update(r=ask(client, "slow one")))
    worker.start()
    assert entered.wait(5)
    second = ask(client, "impatient")
    release.set()
    worker.join()
    assert second.status_code == 429
    assert second.json()["message"] == strings.MESSAGES["already_running"]
    assert first["r"].status_code == 200
    assert ask(client, "after").status_code == 200  # free again
