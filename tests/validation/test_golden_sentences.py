"""Golden answers (SPEC 5.3): the composed sentence agrees with the result cells, offline."""

import json
from pathlib import Path

import pytest
import yaml

from askdb.composer import compose, format_cell
from askdb.config import load_semantic_layer
from askdb.executor import Executor
from askdb.llm import FakeLLMClient
from askdb.planner import Answer, Planner

ROOT = Path(__file__).resolve().parents[2]
RECORDED = Path(__file__).parent / "recorded"
CASES = [
    c
    for c in yaml.safe_load((ROOT / "config/validation/cloudmetrics.yaml").read_text("utf-8"))[
        "cases"
    ]
    if "expected_sql" in c and "conversation" not in c
]


@pytest.fixture(scope="module")
def layer():
    return load_semantic_layer(ROOT / "config/databases/cloudmetrics.yaml")


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_sentence_matches_cells(case, layer):
    recorded = json.loads((RECORDED / f"{case['id']}.json").read_text("utf-8"))
    outcome = Planner(FakeLLMClient(list(recorded["replies"])), layer).plan(case["question"])
    assert isinstance(outcome, Answer)
    page = Executor(ROOT / "data/cloudmetrics.db").execute(outcome.validated)
    composed = compose(outcome.interpretation, page)

    assert composed.sentence is not None, composed.note
    assert composed.note is None
    assert len(composed.display_rows) == len(page.rows)

    if page.total == 1:
        for column, value in zip(page.columns, page.rows[0], strict=True):
            assert format_cell(column, value).text in composed.sentence
    else:
        assert f"{page.total:,}" in composed.sentence
