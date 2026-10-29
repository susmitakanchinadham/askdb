"""Golden set (SPEC 11.2). Offline by default: replays recorded model replies through the planner.

Live run (opt-in, uses the network and ANTHROPIC_API_KEY from the environment):
    ASKDB_LIVE=1 uv run pytest tests/validation
Add ASKDB_RECORD=1 to overwrite tests/validation/recorded/ with the live replies.
A follow-up case (`conversation`) plays its prior turns first and hands their interpretations to the
planner, as a session does.
"""

import json
import os
from datetime import date
from pathlib import Path

import pytest
import yaml

from askdb.config import load_semantic_layer
from askdb.db import open_readonly
from askdb.executor import Executor
from askdb.llm import AnthropicClient, FakeLLMClient
from askdb.planner import Answer, Clarify, Planner, Refuse

ROOT = Path(__file__).resolve().parents[2]
RECORDED = Path(__file__).parent / "recorded"
LIVE = os.environ.get("ASKDB_LIVE") == "1"
CASES = yaml.safe_load((ROOT / "config/validation/cloudmetrics.yaml").read_text("utf-8"))["cases"]
SINGLE = [c for c in CASES if "conversation" not in c]
FOLLOW_UPS = [c for c in CASES if "conversation" in c]
OUTCOME_TYPES = {"clarification": Clarify, "declined": Refuse}


@pytest.fixture(scope="module")
def layer():
    return load_semantic_layer(ROOT / "config/databases/cloudmetrics.yaml")


def rows(sql):
    conn = open_readonly(ROOT / "data/cloudmetrics.db")
    try:
        return sorted(repr(r) for r in conn.execute(sql).fetchall())
    finally:
        conn.close()


class Recording:
    """Wraps a live client and keeps its replies."""

    def __init__(self, inner):
        self.inner, self.replies = inner, []

    def complete(self, system, user):
        out = self.inner.complete(system, user)
        self.replies.append(out)
        return out


def make_planner(layer, case):
    if LIVE:
        client = Recording(AnthropicClient())
    else:
        recorded = json.loads((RECORDED / f"{case['id']}.json").read_text("utf-8"))
        client = FakeLLMClient(list(recorded["replies"]))
    return Planner(client, layer), client


def record_live(case, client):
    if LIVE and os.environ.get("ASKDB_RECORD") == "1":
        record = {
            "id": case["id"],
            "question": case["question"],
            "today": str(case["today"]) if "today" in case else None,
            "source": "live recording",
            "replies": client.replies,
        }
        (RECORDED / f"{case['id']}.json").write_text(json.dumps(record, indent=2) + "\n", "utf-8")


def assert_same_rows(outcome, case):
    assert isinstance(outcome, Answer), outcome
    executor = Executor(ROOT / "data/cloudmetrics.db")
    page = executor.execute(outcome.validated)
    assert not page.truncated
    got = list(page.rows)
    for n in range(2, -(-page.total // page.page_size) + 1):
        got += executor.execute(outcome.validated, page=n).rows
    assert sorted(repr(tuple(r)) for r in got) == rows(case["expected_sql"])


@pytest.mark.parametrize("case", SINGLE, ids=[c["id"] for c in SINGLE])
def test_golden_case(case, layer):
    planner, client = make_planner(layer, case)
    today = case.get("today") or date(2026, 10, 21)
    outcome = planner.plan(case["question"], today=today)
    record_live(case, client)

    if "expected_sql" in case:
        assert_same_rows(outcome, case)
    else:
        assert isinstance(outcome, OUTCOME_TYPES[case["expected_outcome"]]), outcome


@pytest.mark.parametrize("case", FOLLOW_UPS, ids=[c["id"] for c in FOLLOW_UPS])
def test_golden_follow_up(case, layer):
    planner, client = make_planner(layer, case)
    today = case.get("today") or date(2026, 10, 21)
    earlier: list[str] = []
    for turn in case["conversation"]:
        first = planner.plan(turn["question"], tuple(earlier), today=today)
        assert isinstance(first, Answer), first
        earlier.append(first.interpretation)

    outcome = planner.plan(case["question"], tuple(earlier), today=today)
    record_live(case, client)

    assert_same_rows(outcome, case)
    if not LIVE:  # the follow-up prompt carried the earlier reading, and no result values
        prompt = client.calls[-1][1]
        assert "Earlier in this conversation" in prompt
        assert all(f"- {reading}" in prompt for reading in earlier)
        assert case["question"] in prompt


def test_every_recorded_file_belongs_to_a_case():
    assert {p.stem for p in RECORDED.glob("*.json")} == {c["id"] for c in CASES}
