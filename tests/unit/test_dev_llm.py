import json
from pathlib import Path

from askdb.dev_llm import ReplayLLMClient
from askdb.prompts import build_user

ROOT = Path(__file__).resolve().parents[2]


def client():
    return ReplayLLMClient(
        ROOT / "config/validation/cloudmetrics.yaml", ROOT / "tests/validation/recorded"
    )


def test_known_questions_get_their_recorded_reply():
    reply = json.loads(client().complete("sys", build_user("What is our current total MRR?")))
    assert reply["type"] == "answer_plan" and "SUM(mrr)" in reply["sql"]


def test_follow_up_turns_use_their_own_replies():
    c = client()
    first = json.loads(c.complete("s", build_user("Break down MRR by plan.")))
    second = json.loads(c.complete("s", build_user("And split that by industry?", ["x"])))
    assert "industry" not in first["sql"] and "industry" in second["sql"]


def test_anything_else_is_declined():
    reply = json.loads(client().complete("s", build_user("What is the weather?")))
    assert reply == {"type": "refuse", "reason": "out_of_scope"}
