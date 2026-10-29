"""The question endpoints: ask, change a chip, report a wrong answer, start a new question.

Who is asking comes from two headers that the company SSO proxy sets (SPEC 6, open item 9); the
service must never be reachable except through that proxy. The role picks a planner built from the
role-filtered semantic layer. Every /ask and chip request writes one audit row before the response
is returned; if the row cannot be written the request fails and no data is returned.

A session (SPEC 7) carries the interpretation lines of earlier answers, so a follow-up goes
through the same planner, checker and executor as any question. A chip change is just such a
follow-up, limited to the alternatives the answer offered.

Responses carry business wording only and never the generated statement. Fixed wording lives in
`askdb.strings`; model-written text (interpretation, clarifying question, choices, chips) is
checked with `strings.is_clean` before it leaves the server.
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
from askdb.feedback import MAX_NOTE, FeedbackRecord
from askdb.planner import CHIP_KINDS, Clarify, Refuse, Unavailable
from askdb.session import AlreadyRunning, LastAnswer, RateLimited, Session

router = APIRouter()
MESSAGES = strings.MESSAGES

USER_HEADER = "X-Askdb-User"
ROLE_HEADER = "X-Askdb-Role"


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    page: int = Field(default=1, ge=1)
    session_id: str | None = Field(default=None, max_length=100)


class ChipRequest(BaseModel):
    session_id: str = Field(max_length=100)
    kind: str = Field(max_length=20)
    value: str = Field(min_length=1, max_length=200)


class SessionRequest(BaseModel):
    session_id: str = Field(max_length=100)


class FeedbackRequest(BaseModel):
    session_id: str = Field(max_length=100)
    note: str | None = Field(default=None, max_length=MAX_NOTE)


def _declined(reason: str, key: str = "refused") -> dict:
    return {"status": "declined", "reason": reason, "message": MESSAGES[key]}


def _who(request: Request) -> tuple[str, str]:
    return (
        request.headers.get(USER_HEADER, "").strip(),
        request.headers.get(ROLE_HEADER, "").strip(),
    )


def _no_access() -> JSONResponse:
    return JSONResponse({"status": "error", "message": MESSAGES["no_access"]}, status_code=403)


@router.post("/ask")
def ask(body: AskRequest, request: Request):
    return _handle(request, body.question.strip(), body.page, body.session_id)


@router.post("/ask/chip")
def change_chip(body: ChipRequest, request: Request):
    return _handle(request, None, 1, body.session_id, chip=(body.kind, body.value.strip()))


@router.post("/session/reset")
def new_question(body: SessionRequest, request: Request):
    user, role = _who(request)
    if not user or role not in request.app.state.planners:
        return _no_access()
    request.app.state.sessions.reset(body.session_id, user, role)
    return {"status": "ok", "message": strings.NEW_QUESTION}


@router.post("/feedback")
def looks_wrong(body: FeedbackRequest, request: Request):
    user, role = _who(request)
    state = request.app.state
    if not user or role not in state.planners:
        return _no_access()
    session, _ = state.sessions.open(body.session_id, user, role)
    last = session.last
    if last is None:
        return JSONResponse({"status": "error", "message": MESSAGES["no_answer"]}, status_code=409)
    state.feedback.write(
        FeedbackRecord(
            user=user,
            role=role,
            question=last.question,
            plan=last.interpretation,
            sql=last.sql,
            glossary_version=state.layer.version,
            row_count=last.row_count,
            note=body.note,
        )
    )
    return {"status": "ok", "message": strings.FEEDBACK_THANKS}


def _chip_question(session: Session, kind: str, value: str) -> str | None:
    """The follow-up for a chip, or None unless the last answer offered exactly that choice."""
    last = session.last
    if last is None or kind not in CHIP_KINDS:
        return None
    if not any(c.kind == kind and value in c.options for c in last.chips):
        return None
    return f"Same as before, but change the {kind} to: {value}"


def _handle(request: Request, question: str | None, page: int, session_id, chip=None):
    started = time.monotonic()
    state = request.app.state
    user, role = _who(request)

    def finish(payload: dict, record: dict, status_code: int, shown_question: str):
        state.audit.write(
            AuditRecord(
                user=user,
                role=role,
                question=shown_question,
                duration_ms=round((time.monotonic() - started) * 1000),
                glossary_version=state.layer.version,
                **record,
            )
        )
        return JSONResponse(payload, status_code=status_code)

    planner = state.planners.get(role) if user else None
    if planner is None:
        body = {"status": "error", "message": MESSAGES["no_access"]}
        record = {"outcome": "error", "reason": "no_access"}
        return finish(body, record, 403, question or "")

    session, ended = state.sessions.open(session_id, user, role)
    if chip is not None:
        question = _chip_question(session, *chip)
        if question is None:
            body = {"status": "error", "message": MESSAGES["no_chip"], "session_id": session.id}
            return finish(body, {"outcome": "error", "reason": "bad_chip"}, 400, f"chip {chip[0]}")

    try:
        with state.limiter.request(user):
            payload, record = _answer(state, planner, session, question, page)
        status_code = 200
    except RateLimited:
        payload = {"status": "error", "message": MESSAGES["rate_limited"]}
        record, status_code = {"outcome": "error", "reason": "rate_limited"}, 429
    except AlreadyRunning:
        payload = {"status": "error", "message": MESSAGES["already_running"]}
        record, status_code = {"outcome": "error", "reason": "already_running"}, 429

    payload["session_id"] = session.id
    if ended:
        payload["notice"] = strings.SESSION_ENDED
    return finish(payload, record, status_code, question)


def _answer(state, planner, session: Session, question: str, page_number: int):
    """Returns (response body, audit fields)."""
    outcome = planner.plan(question, tuple(session.interpretations))

    if isinstance(outcome, Unavailable):
        body = {"status": "unavailable", "message": MESSAGES["unavailable"]}
        return body, {"outcome": "error", "reason": "llm_unavailable"}
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
        return _declined(outcome.reason, key), record
    if isinstance(outcome, Clarify):
        if not all(strings.is_clean(t) for t in [outcome.question, *outcome.options]):
            return _declined("unclear_wording"), {"outcome": "refuse", "reason": "unclear_wording"}
        state.sessions.remember(
            session, f"Asked: {question} (needed to know: {outcome.question})", None
        )
        body = {"status": "clarify", "question": outcome.question, "options": list(outcome.options)}
        return body, {"outcome": "clarify", "plan": outcome.question}

    sql = outcome.validated.sql
    try:
        page = state.executor.execute(outcome.validated, page=page_number)
    except (QueryTimeout, TooBusy, ExecutionError) as exc:
        key, reason = {
            QueryTimeout: ("slow", "slow_search"),
            TooBusy: ("busy", "busy"),
        }.get(type(exc), ("failed", "execution_failed"))
        record = {"outcome": "error", "reason": reason, "sql": sql, "validator_result": "ok"}
        return {"status": "error", "message": MESSAGES[key]}, record

    interpretation = outcome.interpretation if strings.is_clean(outcome.interpretation) else None
    chips = tuple(
        c for c in outcome.chips if all(strings.is_clean(o) for o in (c.kind, *c.options))
    )
    state.sessions.remember(
        session,
        interpretation or f"Question: {question}",
        LastAnswer(question, interpretation, sql, page.total, chips),
    )
    composed = compose(interpretation, page)
    body = {
        "status": "answer",
        "interpretation": interpretation,
        "chips": [{"kind": c.kind, "options": list(c.options)} for c in chips],
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
    return body, record
