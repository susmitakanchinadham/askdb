"""API entry point: `uvicorn askdb.main:app`."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from askdb.config import SemanticLayer, check_against_database, load_semantic_layer
from askdb.db import open_readonly

DEFAULT_CONFIG = Path("config/databases/cloudmetrics.yaml")


def load_checked_layer(config_path: Path, env: dict[str, str] | None = None) -> SemanticLayer:
    """Load the semantic layer and drop anything the live database no longer matches."""
    layer = load_semantic_layer(config_path)
    conn = open_readonly(layer.resolve_db_path(env))
    try:
        return check_against_database(layer, conn)
    finally:
        conn.close()


def create_app(config_path: Path | None = None) -> FastAPI:
    path = config_path or Path(os.environ.get("ASKDB_CONFIG", DEFAULT_CONFIG))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.layer = load_checked_layer(path)
        yield

    app = FastAPI(title="AskDB", lifespan=lifespan)

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
