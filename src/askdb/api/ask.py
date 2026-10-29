"""POST /ask: question in, answer table / clarification / refusal out.

Responses carry business wording only and never the generated statement. The sentence summary
arrives with the composer (slice 5); until then an answer is the interpretation line plus the table.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from askdb.executor import ExecutionError, QueryTimeout, TooBusy
from askdb.planner import Clarify, Refuse, Unavailable

router = APIRouter()

MESSAGES = {
    "unavailable": "I can't answer right now, please try again shortly.",
    "refused": "I can't answer that reliably yet.",
    "failed": "I couldn't find an answer to that. Try rephrasing or narrowing the question.",
    "slow": "That search took too long. Try a shorter period or a narrower question.",
    "busy": "Lots of people are asking questions right now. Please try again shortly.",
}


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    page: int = Field(default=1, ge=1)


@router.post("/ask")
def ask(body: AskRequest, request: Request) -> dict:
    state = request.app.state
    outcome = state.planner.plan(body.question.strip())

    if isinstance(outcome, Unavailable):
        return {"status": "unavailable", "message": MESSAGES["unavailable"]}
    if isinstance(outcome, Refuse):
        key = "failed" if outcome.reason == "validation_failed" else "refused"
        return {"status": "declined", "reason": outcome.reason, "message": MESSAGES[key]}
    if isinstance(outcome, Clarify):
        return {"status": "clarify", "question": outcome.question, "options": list(outcome.options)}

    try:
        page = state.executor.execute(outcome.validated, page=body.page)
    except QueryTimeout:
        return {"status": "error", "message": MESSAGES["slow"]}
    except TooBusy:
        return {"status": "error", "message": MESSAGES["busy"]}
    except ExecutionError:
        return {"status": "error", "message": MESSAGES["failed"]}
    return {
        "status": "answer",
        "interpretation": outcome.interpretation,
        "columns": page.columns,
        "rows": [list(r) for r in page.rows],
        "page": page.page,
        "page_size": page.page_size,
        "total": page.total,
        "truncated": page.truncated,
        "as_of": page.as_of.isoformat(),
    }
