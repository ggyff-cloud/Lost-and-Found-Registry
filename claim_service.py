"""Claim rules for the Lost & Found Claim Registry.

Everything the product promises lives in this module: an item can be claimed
once, a claim is written inside one transaction, and the database refuses the
second writer even if two requests arrive at the same instant. The Flask layer
only translates the result into a status code, so the same code paths are
exercised by the web tests and by the benchmark script.

The two rules and where they are enforced:

* one claim per item -> ``claims UNIQUE(item_id)`` in ``schema.sql``;
* the claim and the status change are atomic -> ``BEGIN IMMEDIATE`` here.

Raising instead of returning ``None`` keeps the caller honest: a missing item
and a lost race are different outcomes and must reach the client as 404 and
409 respectively.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

ISO = "%Y-%m-%dT%H:%M:%SZ"


class ServiceError(Exception):
    """A request the service refuses, carrying the status the web layer returns."""

    status = 400

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        if status is not None:
            self.status = status


class NotFound(ServiceError):
    """The referenced item does not exist."""

    status = 404


class Conflict(ServiceError):
    """The item is not in a state that allows the requested change."""

    status = 409


class Forbidden(ServiceError):
    """The caller is not the role that may make this change."""

    status = 403


def _now() -> str:
    """Return the current UTC time in the format stored in the database."""
    return datetime.now(timezone.utc).strftime(ISO)


def register_item(
    conn: sqlite3.Connection,
    *,
    name: str,
    place: str,
    occurred_at: str,
    description: str = "",
    reporter_id: str,
) -> int:
    """Record a lost item.

    Args:
        conn: Open connection.
        name: Short item name shown in the list.
        place: Where the item was lost.
        occurred_at: When it was lost, as an ISO 8601 string.
        description: Optional free text.
        reporter_id: Synthetic user id of the reporter.

    Returns:
        The id of the new item, which is now visible in the unclaimed list.
    """
    if not name.strip() or not place.strip():
        raise ServiceError("name and place are required", status=422)
    cursor = conn.execute(
        "INSERT INTO items (name, description, place, occurred_at, status, reporter_id, created_at)"
        " VALUES (?, ?, ?, ?, 'Unclaimed', ?, ?)",
        (name.strip(), description.strip(), place.strip(), occurred_at, reporter_id, _now()),
    )
    return int(cursor.lastrowid)


def list_unclaimed(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return every item that no one has claimed yet.

    Args:
        conn: Open connection.

    Returns:
        Rows ordered by newest first; only ``Unclaimed`` items are included.
    """
    return list(conn.execute(
        "SELECT id, name, place, occurred_at, created_at FROM items"
        " WHERE status = 'Unclaimed' ORDER BY id DESC"
    ))


def list_items(conn: sqlite3.Connection, status: str | None = None) -> list[sqlite3.Row]:
    """Return items for the list view, newest first, optionally filtered by state.

    Args:
        conn: Open connection.
        status: ``Unclaimed``, ``Claimed`` or ``Returned``; ``None`` returns
            every item regardless of state.

    Returns:
        Rows carrying ``status`` and the ``claimant_id`` of the claim when one
        exists, so a claimed item stays visible after it leaves the unclaimed
        list.
    """
    sql = (
        "SELECT i.id, i.name, i.place, i.occurred_at, i.status, c.claimant_id"
        "  FROM items i LEFT JOIN claims c ON c.item_id = i.id"
    )
    if status is None:
        return list(conn.execute(sql + " ORDER BY i.id DESC"))
    return list(conn.execute(sql + " WHERE i.status = ? ORDER BY i.id DESC", (status,)))


def get_item(conn: sqlite3.Connection, item_id: int) -> dict[str, Any]:
    """Return one item together with the claim that exists for it, if any.

    Args:
        conn: Open connection.
        item_id: Item to read.

    Returns:
        A dict with the item fields plus ``claimant_id`` (``None`` when unclaimed).

    Raises:
        NotFound: The item does not exist.
    """
    row = conn.execute(
        "SELECT i.id, i.name, i.description, i.place, i.occurred_at, i.status, i.reporter_id,"
        "       c.claimant_id, c.created_at AS claimed_at"
        "  FROM items i LEFT JOIN claims c ON c.item_id = i.id"
        " WHERE i.id = ?",
        (item_id,),
    ).fetchone()
    if row is None:
        raise NotFound(f"item {item_id} does not exist")
    return dict(row)


def claim_item(conn: sqlite3.Connection, item_id: int, claimant_id: str) -> dict[str, Any]:
    """Claim an item, or fail without leaving a partial write behind.

    The availability check and the insert share one ``BEGIN IMMEDIATE``
    transaction, and the insert also has to satisfy ``UNIQUE(item_id)``. If a
    second request wins the lock first, this call sees a claimed item and
    raises :class:`Conflict`; if both somehow reach the insert, SQLite raises
    ``IntegrityError`` and the transaction is rolled back.

    Args:
        conn: Open connection.
        item_id: Item to claim.
        claimant_id: Synthetic user id of the claimant.

    Returns:
        The stored claim as ``{"item_id", "claimant_id", "created_at"}``.

    Raises:
        NotFound: The item does not exist.
        Conflict: The item is already claimed, returned, or taken mid-transaction.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT status FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise NotFound(f"item {item_id} does not exist")
        if row["status"] != "Unclaimed":
            raise Conflict(f"item {item_id} is {row['status']}, not claimable")

        created_at = _now()
        conn.execute(
            "INSERT INTO claims (item_id, claimant_id, created_at) VALUES (?, ?, ?)",
            (item_id, claimant_id, created_at),
        )
        conn.execute("UPDATE items SET status = 'Claimed' WHERE id = ?", (item_id,))
        conn.execute("COMMIT")
    except sqlite3.IntegrityError as exc:  # UNIQUE(item_id) or an unknown user
        conn.execute("ROLLBACK")
        message = str(exc)
        if "FOREIGN KEY" in message:
            raise ServiceError("unknown claimant", status=422) from exc
        raise Conflict(f"item {item_id} was claimed by another request") from exc
    except BaseException:
        conn.execute("ROLLBACK")
        raise

    return {"item_id": item_id, "claimant_id": claimant_id, "created_at": created_at}


def confirm_return(
    conn: sqlite3.Connection, item_id: int, confirmed_by: str | None = None
) -> dict[str, Any]:
    """Mark a claimed item as returned, closing it to further claims.

    Args:
        conn: Open connection.
        item_id: Item the coordinator has handed over.
        confirmed_by: Identifier of the person confirming. When given it must
            name a coordinator, because closing an item is the coordinator's
            step and not the claimant's.

    Returns:
        ``{"item_id": ..., "status": "Returned"}``.

    Raises:
        NotFound: The item does not exist.
        Conflict: The item is not currently claimed.
        Forbidden: ``confirmed_by`` names someone who is not a coordinator.
        ServiceError: ``confirmed_by`` names no known user.
    """
    if confirmed_by is not None:
        user = conn.execute(
            "SELECT role FROM users WHERE id = ?", (confirmed_by,)
        ).fetchone()
        if user is None:
            raise ServiceError(f"unknown user {confirmed_by}", status=422)
        if user["role"] != "coordinator":
            raise Forbidden(
                f"{confirmed_by} is a {user['role']}, and only a coordinator "
                "can confirm a handover"
            )
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT status FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise NotFound(f"item {item_id} does not exist")
        if row["status"] != "Claimed":
            raise Conflict(f"item {item_id} is {row['status']}, not awaiting a return")
        conn.execute("UPDATE items SET status = 'Returned' WHERE id = ?", (item_id,))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"item_id": item_id, "status": "Returned"}


def claim_count(conn: sqlite3.Connection, item_id: int) -> int:
    """Count the claims stored for one item; used by tests and the benchmark.

    Args:
        conn: Open connection.
        item_id: Item to inspect.

    Returns:
        Number of rows in ``claims`` for that item; the invariant is at most 1.
    """
    row = conn.execute("SELECT COUNT(*) AS n FROM claims WHERE item_id = ?", (item_id,)).fetchone()
    return int(row["n"])
