"""POST /ask: question in, answer / clarification / refusal out.

Responses carry business wording only and never the generated statement. Fixed wording lives in
`askdb.strings`; model-written text (interpretation, clarifying question and choices) is checked
with `strings.is_clean` before it leaves the server.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from askdb import strings
from askdb.composer import compose
from askdb.executor import ExecutionError, QueryTimeout, TooBusy
from askdb.planner import Clarify, Refuse, Unavailable

router = APIRouter()
MESSAGES = strings.MESSAGES


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    page: int = Field(default=1, ge=1)


def _declined(reason: str, key: str = "refused") -> dict:
    return {"status": "declined", "reason": reason, "message": MESSAGES[key]}


@router.post("/ask")
def ask(body: AskRequest, request: Request) -> dict:
    state = request.app.state
    outcome = state.planner.plan(body.question.strip())

    if isinstance(outcome, Unavailable):
        return {"status": "unavailable", "message": MESSAGES["unavailable"]}
    if isinstance(outcome, Refuse):
        key = "failed" if outcome.reason == "validation_failed" else "refused"
        return _declined(outcome.reason, key)
    if isinstance(outcome, Clarify):
        texts = [outcome.question, *outcome.options]
        if not all(strings.is_clean(t) for t in texts):
            return _declined("unclear_wording")
        return {"status": "clarify", "question": outcome.question, "options": list(outcome.options)}

    try:
        page = state.executor.execute(outcome.validated, page=body.page)
    except QueryTimeout:
        return {"status": "error", "message": MESSAGES["slow"]}
    except TooBusy:
        return {"status": "error", "message": MESSAGES["busy"]}
    except ExecutionError:
        return {"status": "error", "message": MESSAGES["failed"]}

    interpretation = outcome.interpretation if strings.is_clean(outcome.interpretation) else None
    composed = compose(interpretation, page)
    return {
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
