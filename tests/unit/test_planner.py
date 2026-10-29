import json
import urllib.error
from datetime import date
from pathlib import Path

import pytest

from askdb.config import load_semantic_layer, render_model_text
from askdb.llm import AnthropicClient, FakeLLMClient, LLMUnavailable
from askdb.planner import Answer, Clarify, Planner, Refuse, Unavailable

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 10, 21)

GOOD = {
    "type": "answer_plan",
    "interpretation": "Active MRR",
    "sql": "SELECT SUM(mrr) AS total_mrr FROM subscriptions WHERE status = 'active'",
}


@pytest.fixture(scope="module")
def layer():
    return load_semantic_layer(ROOT / "config/databases/cloudmetrics.yaml")


def planner(layer, *replies):
    client = FakeLLMClient([r if isinstance(r, str) else json.dumps(r) for r in replies])
    return Planner(client, layer), client


def test_plan(layer):
    p, client = planner(layer, GOOD)
    out = p.plan("What is our MRR?", today=TODAY)
    assert isinstance(out, Answer)
    assert out.interpretation == "Active MRR"
    assert out.validated.ok and len(client.calls) == 1


def test_fenced_json_is_accepted(layer):
    p, _ = planner(layer, "```json\n" + json.dumps(GOOD) + "\n```")
    assert isinstance(p.plan("MRR?", today=TODAY), Answer)


def test_clarify(layer):
    reply = {"type": "clarify", "question": "Which period?", "options": ["2024", "2025"]}
    out = planner(layer, reply)[0].plan("Revenue?", today=TODAY)
    assert out == Clarify("Which period?", ("2024", "2025"))


def test_refuse_keeps_known_reasons_and_defaults_unknown_ones(layer):
    assert planner(layer, {"type": "refuse", "reason": "data_change"})[0].plan("x") == Refuse(
        "data_change"
    )
    assert planner(layer, {"type": "refuse", "reason": "because"})[0].plan("x") == Refuse(
        "out_of_scope"
    )


def test_rejected_sql_is_retried_once_with_the_validator_message(layer):
    bad = {**GOOD, "sql": "DROP TABLE plans"}
    p, client = planner(layer, bad, GOOD)
    out = p.plan("MRR?", today=TODAY)
    assert isinstance(out, Answer) and len(client.calls) == 2
    first_user, retry_user = client.calls[0][1], client.calls[1][1]
    assert "rejected" not in first_user
    assert "not_select" in retry_user and "rejected" in retry_user
    assert client.calls[0][0] == client.calls[1][0]  # same system prompt


def test_two_failures_refuse_and_do_not_retry_again(layer):
    bad = {**GOOD, "sql": "SELECT secret FROM plans"}
    p, client = planner(layer, bad, bad, GOOD)
    out = p.plan("MRR?", today=TODAY)
    assert out == Refuse("validation_failed", "unknown_column", bad["sql"])
    assert len(client.calls) == 2


@pytest.mark.parametrize(
    "reply",
    [
        "not json at all",
        '["a list"]',
        '{"type": "mystery"}',
        {"type": "clarify", "question": "Which?", "options": ["only one"]},
        {"type": "answer_plan", "interpretation": "x"},
    ],
)
def test_malformed_replies_get_one_retry(layer, reply):
    p, client = planner(layer, reply, GOOD)
    assert isinstance(p.plan("MRR?", today=TODAY), Answer)
    assert "bad_reply" in client.calls[1][1]


def test_outage_is_reported_without_retry_or_guessing(layer):
    class Down:
        calls = 0

        def complete(self, system, user):
            Down.calls += 1
            raise LLMUnavailable("down")

    assert Planner(Down(), layer).plan("MRR?") == Unavailable()
    assert Down.calls == 1


def test_prompt_holds_glossary_question_and_prior_interpretations_only(layer):
    p, client = planner(layer, GOOD)
    p.plan("What is our MRR?", prior_interpretations=["Active MRR by plan"], today=TODAY)
    system, user = client.calls[0]
    assert render_model_text(layer) in system
    assert "2026-10-21" in system
    assert "What is our MRR?" in user and "- Active MRR by plan" in user


def test_prompt_lists_the_allowed_functions_and_the_reply_shapes(layer):
    p, client = planner(layer, GOOD)
    p.plan("MRR?", today=TODAY)
    system = client.calls[0][0]
    for needle in ("answer_plan", "clarify", "refuse", "strftime", "SELECT"):
        assert needle in system


class _Response:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, *a):
        return self.body


def test_live_client_returns_the_text_blocks(monkeypatch):
    body = json.dumps({"content": [{"type": "text", "text": "hello"}]}).encode()
    seen = {}

    def fake_urlopen(request, timeout):
        seen["headers"] = dict(request.header_items())
        seen["body"] = json.loads(request.data)
        return _Response(body)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = AnthropicClient(api_key="k", model="m").complete("sys", "usr")
    assert out == "hello"
    assert seen["body"]["model"] == "m" and seen["body"]["system"] == "sys"
    assert seen["headers"]["X-api-key"] == "k"


@pytest.mark.parametrize("error", [urllib.error.URLError("x"), TimeoutError(), OSError()])
def test_live_client_failures_become_unavailable(monkeypatch, error):
    def boom(request, timeout):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(LLMUnavailable):
        AnthropicClient(api_key="k").complete("s", "u")


def test_live_client_without_a_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable):
        AnthropicClient().complete("s", "u")
