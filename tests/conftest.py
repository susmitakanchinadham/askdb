from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def config_path() -> Path:
    return ROOT / "config" / "databases" / "cloudmetrics.yaml"


@pytest.fixture
def db_path() -> Path:
    return ROOT / "data" / "cloudmetrics.db"


@pytest.fixture(autouse=True)
def audit_path(tmp_path, monkeypatch) -> Path:
    """Keep the audit log out of the repository for every test."""
    path = tmp_path / "audit.db"
    monkeypatch.setenv("ASKDB_AUDIT_PATH", str(path))
    return path
