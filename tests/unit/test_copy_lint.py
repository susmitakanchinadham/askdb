import json

import pytest

from askdb import strings

BANNED_SAMPLES = [
    "SQL",
    "sql",
    "query",
    "Queries",
    "table",
    "Tables",
    "column",
    "columns",
    "timeout",
    "time-out",
    "timeouts",
    "schema",
    "stack trace",
]


@pytest.mark.parametrize("word", BANNED_SAMPLES)
def test_detector_catches_each_banned_word(word):
    assert strings.find_banned(f"Check the {word} please")
    assert not strings.is_clean(f"Check the {word} please")


@pytest.mark.parametrize("text", ["Sales by month", "Tablet sales", "Columnar", "Squirrel"])
def test_detector_ignores_words_that_only_contain_a_banned_word(text):
    assert strings.is_clean(text)


def test_every_fixed_user_facing_string_is_clean():
    offenders = {s: strings.find_banned(s) for s in strings.ALL_STRINGS if strings.find_banned(s)}
    assert not offenders


def test_all_strings_covers_every_string_constant_in_the_module():
    declared = set()
    for name, value in vars(strings).items():
        if name.startswith("_") or name in ("ALL_STRINGS", "BANNED"):
            continue
        if isinstance(value, str):
            declared.add(value)
        elif isinstance(value, dict) and all(isinstance(v, str) for v in value.values()):
            declared.update(value.values())
    assert declared <= set(strings.ALL_STRINGS), declared - set(strings.ALL_STRINGS)


def walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            if k not in ("as_of", "reason", "status"):  # codes and timestamps, not wording
                yield from walk_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk_strings(v)


def make_client(config_path, db_path, monkeypatch, llm, raise_server_exceptions=True):
    from fastapi.testclient import TestClient

    from askdb.main import create_app

    monkeypatch.setenv("CLOUDMETRICS_DB_PATH", str(db_path))
    return TestClient(
        create_app(config_path, llm=llm),
        raise_server_exceptions=raise_server_exceptions,
        headers={"X-Askdb-User": "tester", "X-Askdb-Role": "admin"},
    )


def reply(**kw):
    return json.dumps(kw)


def test_every_api_response_and_error_is_clean(config_path, db_path, monkeypatch):
    from askdb.llm import FakeLLMClient, LLMUnavailable

    good = reply(
        type="answer_plan", interpretation="Plans", sql="SELECT plan_name, max_users FROM plans"
    )
    slow = reply(
        type="answer_plan",
        interpretation="All pairs",
        sql="SELECT COUNT(*) FROM invoices a, invoices b, invoices c, invoices d",
    )
    cases = {
        "answer": FakeLLMClient([good]),
        "refuse": FakeLLMClient([reply(type="refuse", reason="data_change")]),
        "clarify": FakeLLMClient(
            [reply(type="clarify", question="Which month?", options=["a", "b"])]
        ),
        "validation": FakeLLMClient(
            [reply(type="answer_plan", interpretation="x", sql="DROP TABLE a")] * 2
        ),
        "slow": FakeLLMClient([slow]),
    }

    class Down:
        def complete(self, system, user):
            raise LLMUnavailable("down")

    cases["outage"] = Down()
    seen = []
    for name, llm in cases.items():
        with make_client(config_path, db_path, monkeypatch, llm) as client:
            if name == "slow":
                client.app.state.executor.timeout = 0.2
            body = client.post("/ask", json={"question": "anything"}).json()
            seen.append((name, body))
    with make_client(config_path, db_path, monkeypatch, cases["answer"]) as client:
        resp = client.post("/ask", json={"question": ""})
        assert resp.status_code == 422
        seen.append(("invalid", resp.json()))

    assert {b["status"] for _, b in seen} >= {
        "answer",
        "declined",
        "clarify",
        "unavailable",
        "error",
    }
    for name, body in seen:
        offenders = [s for s in walk_strings(body) if strings.find_banned(s)]
        assert not offenders, (name, offenders)


def test_unexpected_failure_returns_a_clean_message(config_path, db_path, monkeypatch):
    from askdb.llm import FakeLLMClient

    with make_client(
        config_path, db_path, monkeypatch, FakeLLMClient([]), raise_server_exceptions=False
    ) as client:
        client.app.state.planners = None  # makes the handler blow up
        resp = client.post("/ask", json={"question": "x"})
    assert resp.status_code == 500
    assert resp.json() == {"status": "error", "message": strings.MESSAGES["unexpected"]}


def test_model_written_text_with_banned_wording_is_not_passed_on(config_path, db_path, monkeypatch):
    from askdb.llm import FakeLLMClient

    llm = FakeLLMClient(
        [reply(type="clarify", question="Which table do you mean?", options=["plans", "invoices"])]
    )
    with make_client(config_path, db_path, monkeypatch, llm) as client:
        body = client.post("/ask", json={"question": "x"}).json()
    assert body["status"] == "declined"
    assert body["message"] == strings.MESSAGES["refused"]

    llm = FakeLLMClient(
        [
            reply(
                type="answer_plan",
                interpretation="From the plans table",
                sql="SELECT plan_name FROM plans",
            )
        ]
    )
    with make_client(config_path, db_path, monkeypatch, llm) as client:
        body = client.post("/ask", json={"question": "x"}).json()
    assert body["status"] == "answer" and body["interpretation"] is None
    assert not any(strings.find_banned(s) for s in walk_strings(body))
