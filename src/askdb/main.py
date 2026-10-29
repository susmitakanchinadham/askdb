"""API entry point: `uvicorn askdb.main:app`."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from askdb import strings
from askdb.api.ask import router as ask_router
from askdb.audit import AuditLog
from askdb.config import SemanticLayer, check_against_database, load_semantic_layer
from askdb.db import open_readonly
from askdb.executor import Executor
from askdb.feedback import FeedbackLog
from askdb.llm import AnthropicClient, LLMClient
from askdb.planner import Planner
from askdb.roles import hidden_terms, layer_for_role, load_roles
from askdb.session import RATE_PER_MINUTE, SessionStore, UserLimiter
from askdb.validator import allowed_from_layer

DEFAULT_CONFIG = Path("config/databases/cloudmetrics.yaml")
DEFAULT_ROLES = Path("config/roles/cloudmetrics.yaml")
DEFAULT_AUDIT = Path("data/audit.db")


def load_checked_layer(config_path: Path, env: dict[str, str] | None = None) -> SemanticLayer:
    """Load the semantic layer and drop anything the live database no longer matches."""
    layer = load_semantic_layer(config_path)
    conn = open_readonly(layer.resolve_db_path(env))
    try:
        return check_against_database(layer, conn)
    finally:
        conn.close()


def create_app(
    config_path: Path | None = None,
    llm: LLMClient | None = None,
    roles_path: Path | None = None,
    audit_path: Path | None = None,
) -> FastAPI:
    path = config_path or Path(os.environ.get("ASKDB_CONFIG", DEFAULT_CONFIG))
    roles_file = roles_path or Path(os.environ.get("ASKDB_ROLES", DEFAULT_ROLES))
    audit_file = audit_path or Path(os.environ.get("ASKDB_AUDIT_PATH", DEFAULT_AUDIT))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        layer = load_checked_layer(path)
        app.state.layer = layer
        # Without an API key the client reports an outage, so /ask degrades to a friendly message.
        client = llm or AnthropicClient()
        book = load_roles(roles_file, layer)
        app.state.planners = {
            name: Planner(
                client,
                layer_for_role(layer, role),
                allowed_from_layer(layer_for_role(layer, role)),
                hidden_terms(layer, role),
            )
            for name, role in book.roles.items()
        }
        app.state.audit = AuditLog(audit_file)
        app.state.feedback = FeedbackLog(audit_file)
        app.state.sessions = SessionStore()
        app.state.limiter = UserLimiter(
            int(os.environ.get("ASKDB_RATE_PER_MINUTE", RATE_PER_MINUTE))
        )
        app.state.executor = Executor(layer.resolve_db_path())
        yield

    app = FastAPI(title="AskDB", lifespan=lifespan)
    app.include_router(ask_router)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return JSONResponse(
            {"status": "error", "message": strings.MESSAGES["invalid"]}, status_code=422
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        return JSONResponse(
            {"status": "error", "message": strings.MESSAGES["unexpected"]}, status_code=500
        )

    @app.get("/health")
    def health() -> dict:
        layer: SemanticLayer = app.state.layer
        return {
            "status": "ok",
            "database": layer.display_name,
            "version": layer.version,
            "issues": list(layer.issues),
        }

    return app


app = create_app()
