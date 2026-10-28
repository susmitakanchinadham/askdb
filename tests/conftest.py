from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def config_path() -> Path:
    return ROOT / "config" / "databases" / "cloudmetrics.yaml"


@pytest.fixture
def db_path() -> Path:
    return ROOT / "data" / "cloudmetrics.db"
