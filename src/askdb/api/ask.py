"""POST /ask: question in, answer / clarification / refusal out.

Who is asking comes from two headers that the company SSO proxy sets (SPEC 6, open item 9); the
service must never be reachable except through that proxy. The role picks a planner built from the
role-filtered semantic layer. Every request writes one audit row before the response is returned;
if the row cannot be written the request fails and no data is returned.

Responses carry business wording only and never the generated statement. Fixed wording lives in
`askdb.strings`; model-written text (interpretation, clarifying question and choices) is checked
with `strings.is_clean` before it leaves the server.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from askdb import strings
from askdb.audit import AuditRecord
from askdb.composer import compose
from askdb.executor import ExecutionError, QueryTimeout, TooBusy
from askdb.planner import Clarify, Refuse, Unavailable

router = APIRouter()
MESSAGES = strings.MESSAGES

USER_HEADER = "X-Askdb-User"
ROLE_HEADER = "X-Askdb-Role"


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    page: int = Field(default=1, ge=1)


def _declined(reason: str, key: str = "refused") -> dict:
    return {"status": "declined", "reason": reason, "message": MESSAGES[key]}


@router.post("/ask")
def ask(body: AskRequest, request: Request):
    started = time.monotonic()
    state = request.app.state
    question = body.question.strip()
    user = request.headers.get(USER_HEADER, "").strip()
    role = request.headers.get(ROLE_HEADER, "").strip()

    payload, record, status_code = _answer(state, user, role, question, body.page)
    state.audit.write(
        AuditRecord(
            user=user,
            role=role,
            question=question,
            duration_ms=round((time.monotonic() - started) * 1000),
            glossary_version=state.layer.version,
            **record,
        )
    )
    return JSONResponse(payload, status_code=status_code)


def _answer(state, user: str, role: str, question: str, page_number: int):
    """Returns (response body, audit fields, HTTP status)."""
    planner = state.planners.get(role) if user else None
    if planner is None:
        body = {"status": "error", "message": MESSAGES["no_access"]}
        return body, {"outcome": "error", "reason": "no_access"}, 403

    outcome = planner.plan(question)

    if isinstance(outcome, Unavailable):
        body = {"status": "unavailable", "message": MESSAGES["unavailable"]}
        return body, {"outcome": "error", "reason": "llm_unavailable"}, 200
    if isinstance(outcome, Refuse):
        if outcome.reason == "not_permitted":
            key = "not_permitted"
        elif outcome.reason == "validation_failed":
            key = "failed"
        else:
            key = "refused"
        record = {
            "outcome": "refuse",
            "reason": outcome.reason,
            "sql": outcome.rejected_sql,
            "validator_result": outcome.validator_code,
        }
        return _declined(outcome.reason, key), record, 200
    if isinstance(outcome, Clarify):
        if not all(strings.is_clean(t) for t in [outcome.question, *outcome.options]):
            return (
                _declined("unclear_wording"),
                {"outcome": "refuse", "reason": "unclear_wording"},
                200,
            )
        body = {"status": "clarify", "question": outcome.question, "options": list(outcome.options)}
        return body, {"outcome": "clarify", "plan": outcome.question}, 200

    sql = outcome.validated.sql
    try:
        page = state.executor.execute(outcome.validated, page=page_number)
    except (QueryTimeout, TooBusy, ExecutionError) as exc:
        key, reason = {
            QueryTimeout: ("slow", "slow_search"),
            TooBusy: ("busy", "busy"),
        }.get(type(exc), ("failed", "execution_failed"))
        record = {"outcome": "error", "reason": reason, "sql": sql, "validator_result": "ok"}
        return {"status": "error", "message": MESSAGES[key]}, record, 200

    interpretation = outcome.interpretation if strings.is_clean(outcome.interpretation) else None
    composed = compose(interpretation, page)
    body = {
        "status": "answer",
        "interpretation": interpretation,
        "sentence": composed.sentence,
        "note": composed.note,
        "columns": composed.display_columns,
        "rows": composed.display_rows,
        "page": page.page,
        "page_size": page.page_size,
        "total": page.total,
        "truncated": page.truncated,
        "truncation_note": composed.truncation_note,
        "as_of": page.as_of.isoformat(),
    }
    record = {
        "outcome": "answer",
        "plan": interpretation,
        "sql": sql,
        "row_count": page.total,
        "validator_result": "ok",
    }
    return body, record, 200
