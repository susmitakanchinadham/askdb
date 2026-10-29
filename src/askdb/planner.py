"""Planner (SPEC 5): question in, one of answer / clarify / refuse / unavailable out.

The model's SQL is only ever a proposal. It is validated against the allowed tables and columns,
and on a rejection the model gets exactly one retry with the validator's code and message. Those
messages are for the model; user-facing wording is chosen by the caller from the outcome kind.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from askdb.config import SemanticLayer, render_model_text
from askdb.llm import LLMClient, LLMUnavailable
from askdb.prompts import REFUSE_REASONS, build_system, build_user
from askdb.validator import Allowed, Code, ValidationResult, allowed_from_layer, validate_sql

MAX_OPTIONS = 4
MIN_OPTIONS = 2


CHIP_KINDS = ("period", "measure", "grouping")


@dataclass(frozen=True)
class Chip:
    kind: str  # one of CHIP_KINDS
    options: tuple[str, ...]


@dataclass(frozen=True)
class Answer:
    interpretation: str
    validated: ValidationResult  # `.sql` is internal; never put it in a response
    chips: tuple[Chip, ...] = ()


@dataclass(frozen=True)
class Clarify:
    question: str
    options: tuple[str, ...]


@dataclass(frozen=True)
class Refuse:
    reason: str  # one of REFUSE_REASONS, or "validation_failed" after two rejections
    validator_code: str | None = None  # for the audit log
    rejected_sql: str | None = None  # the last statement the checker refused (audit only)


@dataclass(frozen=True)
class Unavailable:
    pass


Outcome = Answer | Clarify | Refuse | Unavailable

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


class _BadReply(Exception):
    def __init__(self, code: str, message: str, sql: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.sql = sql


def _parse(text: str) -> dict:
    try:
        data = json.loads(_FENCE.sub("", text.strip()))
    except ValueError as exc:
        raise _BadReply("bad_reply", "The reply was not a single JSON object.") from exc
    if not isinstance(data, dict) or "type" not in data:
        raise _BadReply("bad_reply", "The reply must be a JSON object with a 'type' field.")
    return data


def _chips(raw: object) -> tuple[Chip, ...]:
    """Optional change-chips from the model; anything malformed is dropped, never retried."""
    out: dict[str, Chip] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("kind") not in CHIP_KINDS:
            continue
        options = item.get("options")
        if not isinstance(options, list):
            continue
        kept = tuple(dict.fromkeys(o.strip() for o in options if isinstance(o, str) and o.strip()))
        if len(kept) >= MIN_OPTIONS and item["kind"] not in out:
            out[item["kind"]] = Chip(item["kind"], kept[:MAX_OPTIONS])
    return tuple(out.values())


class Planner:
    def __init__(
        self,
        client: LLMClient,
        layer: SemanticLayer,
        allowed: Allowed | None = None,
        hidden: frozenset[str] = frozenset(),
    ):
        """`layer` and `allowed` are already narrowed to the role; `hidden` is what was removed."""
        self.client = client
        self._hidden = (
            re.compile(r"\b(?:" + "|".join(map(re.escape, sorted(hidden))) + r")\b", re.IGNORECASE)
            if hidden
            else None
        )
        self.model_text = render_model_text(layer)
        self.allowed = allowed if allowed is not None else allowed_from_layer(layer)
        self.glossary_version = layer.version

    def plan(
        self,
        question: str,
        prior_interpretations: Sequence[str] = (),
        today: date | None = None,
    ) -> Outcome:
        system = build_system(self.model_text, today or date.today())
        rejection: tuple[str, str] | None = None
        last = _BadReply("bad_reply", "")
        for _attempt in range(2):
            user = build_user(question, prior_interpretations, rejection)
            try:
                reply = self.client.complete(system, user)
            except LLMUnavailable:
                return Unavailable()
            try:
                return self._interpret(_parse(reply))
            except _BadReply as bad:
                last = bad
                if self._names_hidden(bad):
                    # Do not echo the hidden name back to the model in a retry prompt.
                    return Refuse("not_permitted", bad.code, bad.sql)
                rejection = (bad.code, bad.message)
        return Refuse("validation_failed", last.code, last.sql)

    def _names_hidden(self, bad: _BadReply) -> bool:
        blocked = (str(Code.TABLE_NOT_ALLOWED), str(Code.UNKNOWN_COLUMN))
        return bool(self._hidden and bad.code in blocked and self._hidden.search(bad.message))

    def _interpret(self, data: dict) -> Outcome:
        kind = data["type"]
        if kind == "refuse":
            reason = data.get("reason")
            return Refuse(reason if reason in REFUSE_REASONS else "out_of_scope")
        if kind == "clarify":
            question, options = data.get("question"), data.get("options")
            if (
                not isinstance(question, str)
                or not isinstance(options, list)
                or not MIN_OPTIONS <= len(options) <= MAX_OPTIONS
                or not all(isinstance(o, str) and o for o in options)
            ):
                raise _BadReply("bad_reply", "A clarify reply needs a question and 2 to 4 options.")
            return Clarify(question, tuple(options))
        if kind == "answer_plan":
            interpretation, sql = data.get("interpretation"), data.get("sql")
            if not isinstance(interpretation, str) or not isinstance(sql, str):
                raise _BadReply("bad_reply", "An answer_plan needs 'interpretation' and 'sql'.")
            result = validate_sql(sql, self.allowed)
            if not result.ok:
                raise _BadReply(str(result.code), result.message, sql)
            return Answer(interpretation, result, _chips(data.get("chips")))
        raise _BadReply("bad_reply", "'type' must be answer_plan, clarify or refuse.")
