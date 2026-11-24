"""Acceptance tests: one test per backlog item of the prioritised backlog.

The names carry the backlog id so a reviewer can trace a requirement to the
test that proves it, which is what Assignment 2 asks for.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import claim_service as svc


def _new_item(conn: sqlite3.Connection, name: str = "Black umbrella") -> int:
    """Insert one item through the service and return its id."""
    return svc.register_item(
        conn,
        name=name,
        place="Library, 2nd floor",
        occurred_at="2026-11-20",
        description="Folding, wooden handle",
        reporter_id="u001",
    )


def test_b1_register_item_saves_and_appears_in_list(conn: sqlite3.Connection) -> None:
    """B1: the item is saved and appears in the unclaimed list."""
    item_id = _new_item(conn)

    stored = svc.get_item(conn, item_id)
    assert stored["name"] == "Black umbrella"
    assert stored["status"] == "Unclaimed"
    assert [row["id"] for row in svc.list_unclaimed(conn)] == [item_id]


def test_b1_register_rejects_missing_fields(conn: sqlite3.Connection) -> None:
    """B1 (negative): a request without a name is refused, not stored."""
    with pytest.raises(svc.ServiceError):
        svc.register_item(conn, name="  ", place="Library", occurred_at="2026-11-20", reporter_id="u001")


def test_b2_list_shows_only_unclaimed(conn: sqlite3.Connection) -> None:
    """B2: only unclaimed items are listed."""
    first = _new_item(conn, "Umbrella")
    second = _new_item(conn, "Water bottle")
    svc.claim_item(conn, first, "u002")

    listed = [row["id"] for row in svc.list_unclaimed(conn)]
    assert listed == [second]


def test_b3_claim_is_saved_and_status_changes(conn: sqlite3.Connection) -> None:
    """B3: the claim is saved and the status becomes Claimed."""
    item_id = _new_item(conn)

    stored = svc.claim_item(conn, item_id, "u002")

    assert stored["claimant_id"] == "u002"
    assert svc.get_item(conn, item_id)["status"] == "Claimed"
    assert svc.claim_count(conn, item_id) == 1


def test_b3_second_claim_on_the_same_item_is_refused(conn: sqlite3.Connection) -> None:
    """B3 (negative): a claimed item cannot be claimed again."""
    item_id = _new_item(conn)
    svc.claim_item(conn, item_id, "u002")

    with pytest.raises(svc.Conflict):
        svc.claim_item(conn, item_id, "u003")

    assert svc.claim_count(conn, item_id) == 1


def test_b4_ten_simultaneous_claims_leave_exactly_one_owner(conn: sqlite3.Connection, db_path: Path) -> None:
    """B4: 10 simultaneous claims -> 1 success and 9 refusals."""
    import threading

    import db as database

    item_id = _new_item(conn)
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt(claimant: str) -> None:
        """Claim from its own connection, the way a separate request would."""
        worker = database.connect(db_path)
        try:
            svc.claim_item(worker, item_id, claimant)
            result = "claimed"
        except svc.Conflict:
            result = "conflict"
        finally:
            worker.close()
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=attempt, args=(f"u{j:03d}",)) for j in range(2, 12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("claimed") == 1
    assert outcomes.count("conflict") == 9
    assert svc.claim_count(conn, item_id) == 1


def test_b5_return_closes_the_item(conn: sqlite3.Connection) -> None:
    """B5: the status becomes Returned and the item cannot be claimed again."""
    item_id = _new_item(conn)
    svc.claim_item(conn, item_id, "u002")

    result = svc.confirm_return(conn, item_id)

    assert result["status"] == "Returned"
    assert svc.get_item(conn, item_id)["status"] == "Returned"
    with pytest.raises(svc.Conflict):
        svc.claim_item(conn, item_id, "u003")


def test_b5_return_requires_a_claim(conn: sqlite3.Connection) -> None:
    """B5 (negative): an unclaimed item cannot be marked as returned."""
    item_id = _new_item(conn)

    with pytest.raises(svc.Conflict):
        svc.confirm_return(conn, item_id)


def test_b6_the_test_job_is_defined() -> None:
    """B6: a Pull Request runs the test job, so a red build blocks the merge."""
    workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "tests.yml"
    text = workflow.read_text(encoding="utf-8")

    assert "pytest" in text
    assert "pull_request" in text


def test_unknown_item_is_404_not_silence(conn: sqlite3.Connection) -> None:
    """A missing item is reported as not found instead of an empty success."""
    with pytest.raises(svc.NotFound):
        svc.get_item(conn, 9999)
    with pytest.raises(svc.NotFound):
        svc.claim_item(conn, 9999, "u002")


def test_api_claim_conflict_returns_409(client) -> None:
    """The JSON API maps a lost race to HTTP 409, not to a 500."""
    created = client.post("/api/items", json={
        "name": "Blue notebook", "place": "Canteen", "occurred_at": "2026-11-20",
    })
    item_id = created.get_json()["id"]

    assert client.post(f"/api/items/{item_id}/claim", json={"claimant_id": "u002"}).status_code == 201
    assert client.post(f"/api/items/{item_id}/claim", json={"claimant_id": "u003"}).status_code == 409
    assert client.get(f"/api/items/{item_id}").get_json()["status"] == "Claimed"
