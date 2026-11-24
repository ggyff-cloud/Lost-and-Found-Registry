"""Shared fixtures: a throw-away database and a Flask test client per test."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db as database  # noqa: E402
from app import create_app  # noqa: E402


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Return a fresh database file path inside the test's temporary directory."""
    return tmp_path / "test.sqlite3"


@pytest.fixture()
def conn(db_path: Path):
    """Yield an initialised connection with the synthetic users in place."""
    connection = database.connect(db_path)
    database.init_db(connection)
    database.seed(connection)
    yield connection
    connection.close()


@pytest.fixture()
def client(db_path: Path):
    """Yield a Flask test client bound to its own database file."""
    app = create_app(str(db_path))
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client
