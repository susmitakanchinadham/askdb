"""API entry point: `uvicorn askdb.main:app`."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from askdb.api.ask import router as ask_router
from askdb.config import SemanticLayer, check_against_database, load_semantic_layer
from askdb.db import open_readonly
from askdb.executor import Executor
from askdb.llm import AnthropicClient, LLMClient
from askdb.planner import Planner
from askdb.validator import allowed_from_layer

DEFAULT_CONFIG = Path("config/databases/cloudmetrics.yaml")


def load_checked_layer(config_path: Path, env: dict[str, str] | None = None) -> SemanticLayer:
    """Load the semantic layer and drop anything the live database no longer matches."""
    layer = load_semantic_layer(config_path)
    conn = open_readonly(layer.resolve_db_path(env))
    try:
        return check_against_database(layer, conn)
    finally:
        conn.close()


def create_app(config_path: Path | None = None, llm: LLMClient | None = None) -> FastAPI:
    path = config_path or Path(os.environ.get("ASKDB_CONFIG", DEFAULT_CONFIG))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        layer = load_checked_layer(path)
        app.state.layer = layer
        # Without an API key the client reports an outage, so /ask degrades to a friendly message.
        app.state.planner = Planner(llm or AnthropicClient(), layer, allowed_from_layer(layer))
        app.state.executor = Executor(layer.resolve_db_path())
        yield

    app = FastAPI(title="AskDB", lifespan=lifespan)
    app.include_router(ask_router)

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
