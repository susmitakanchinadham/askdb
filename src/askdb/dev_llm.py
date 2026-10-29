"""Offline stand-in for the model, for running the app without an API key (development only).

`ASKDB_LLM=replay` makes `main.py` use this client. It answers only the questions in the golden set,
with the replies recorded in `tests/validation/recorded/`; anything else is declined as out of
scope. It exists so the UI can be exercised end to end; it is not a fallback for real use.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from askdb.llm import LLMUnavailable

_QUESTION = re.compile(r"^Question: (.*)$", re.MULTILINE)
_DECLINED = json.dumps({"type": "refuse", "reason": "out_of_scope"})


class ReplayLLMClient:
    def __init__(self, golden: Path, recorded: Path):
        self.by_question: dict[str, str] = {}
        cases = yaml.safe_load(Path(golden).read_text(encoding="utf-8"))["cases"]
        for case in cases:
            path = Path(recorded) / f"{case['id']}.json"
            if not path.exists():
                continue
            replies = json.loads(path.read_text(encoding="utf-8"))["replies"]
            for turn, reply in zip(case.get("conversation", []), replies, strict=False):
                self.by_question.setdefault(turn["question"], reply)
            self.by_question[case["question"]] = replies[-1]

    def complete(self, system: str, user: str) -> str:
        match = _QUESTION.search(user)
        if not match:
            raise LLMUnavailable("no question in prompt")
        return self.by_question.get(match.group(1).strip(), _DECLINED)
