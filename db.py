"""Database helpers for the Lost & Found Claim Registry.

The connection is configured once, and every write goes through an explicit
transaction, so the claim rule is decided by SQLite rather than by the web
layer. ``isolation_level=None`` disables the driver's implicit transaction
handling; the service functions issue BEGIN IMMEDIATE / COMMIT themselves.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB = Path(os.environ.get("LOSTFOUND_DB", Path(__file__).with_name("lostfound.sqlite3")))


def connect(db_path: str | Path | None = None, *, wal: bool = True) -> sqlite3.Connection:
    """Open a connection with the pragmas the service relies on.

    Args:
        db_path: Database file to open; defaults to ``LOSTFOUND_DB`` or ``lostfound.sqlite3``.
        wal: Use write-ahead logging, which lets readers proceed while one writer commits.

    Returns:
        A connection with ``row_factory`` set to ``sqlite3.Row`` and foreign keys enabled.
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if wal:
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create the tables if they do not exist yet.

    Args:
        conn: Open connection whose database should hold the schema.
    """
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def seed(conn: sqlite3.Connection) -> None:
    """Insert the synthetic users used by the demo, tests and benchmarks.

    Only made-up identifiers are used; no real student record is stored.

    Args:
        conn: Open connection whose database should receive the seed rows.
    """
    users = [
        ("u001", "reporter"),
        ("u002", "claimant"),
        ("u003", "claimant"),
        ("u900", "coordinator"),
    ]
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO users (id, role) VALUES (?, ?)",
            users,
        )
