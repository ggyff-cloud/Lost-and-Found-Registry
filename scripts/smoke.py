"""Acceptance smoke test that needs nothing but the standard library.

``pytest`` and the Flask test client cover the same behaviour on a machine with
network access. This file exists so the checks can also be run where those
packages are not installed, and so the report can quote a result produced from
a named environment:

    python scripts/smoke.py

Each check prints ``PASS`` or ``FAIL`` and the process exits non-zero on the
first failure, which makes it usable as a gate.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import claim_service as svc  # noqa: E402
import db as database  # noqa: E402

PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    """Record one assertion and stop the run when it does not hold.

    Args:
        name: Backlog id and expectation, printed on success.
        condition: Value that must be true.
        detail: Extra text printed when the check fails.
    """
    if condition:
        PASSED.append(name)
        print(f"PASS {name}")
        return
    print(f"FAIL {name} {detail}")
    raise SystemExit(1)


def fresh(path: Path) -> sqlite3.Connection:
    """Create a database with the schema and the synthetic users.

    Args:
        path: File to create.

    Returns:
        An open connection.
    """
    if path.exists():
        path.unlink()
    conn = database.connect(path)
    database.init_db(conn)
    database.seed(conn)
    return conn


def main() -> None:
    """Run the acceptance checks in backlog order."""
    work = ROOT / "results" / "smoke"
    work.mkdir(parents=True, exist_ok=True)
    db_path = work / "smoke.sqlite3"
    conn = fresh(db_path)

    # B1 - register and appear in the list
    item_id = svc.register_item(
        conn, name="Black umbrella", place="Library", occurred_at="2026-11-20",
        description="Folding", reporter_id="u001",
    )
    check("B1 item saved and listed", [row["id"] for row in svc.list_unclaimed(conn)] == [item_id])

    # B2 - list shows only unclaimed items
    second = svc.register_item(
        conn, name="Water bottle", place="Canteen", occurred_at="2026-11-20", reporter_id="u001"
    )
    svc.claim_item(conn, item_id, "u002")
    check("B2 only unclaimed listed", [row["id"] for row in svc.list_unclaimed(conn)] == [second])

    # B3 - claim saved, status moves
    third = svc.register_item(
        conn, name="Blue notebook", place="Hall", occurred_at="2026-11-20", reporter_id="u001"
    )
    svc.claim_item(conn, third, "u002")
    detail = svc.get_item(conn, third)
    check("B3 claim saved and status Claimed",
          detail["status"] == "Claimed" and detail["claimant_id"] == "u002")

    # B3 negative - a claimed item refuses a second claimant
    try:
        svc.claim_item(conn, third, "u003")
        refused = False
    except svc.Conflict:
        refused = True
    check("B3 second claim refused with 409", refused)

    # B4 - ten simultaneous claims leave one owner
    target = svc.register_item(
        conn, name="Phone", place="Lab", occurred_at="2026-11-20", reporter_id="u001"
    )
    outcomes: list[str] = []
    guard = threading.Lock()

    conn.executemany(
        "INSERT OR IGNORE INTO users (id, role) VALUES (?, 'claimant')",
        [(f"u9{i:02d}",) for i in range(10)],
    )

    def race(index: int) -> None:
        """Claim from its own connection, as a separate request would."""
        worker = database.connect(db_path)
        try:
            svc.claim_item(worker, target, f"u9{index:02d}")
            result = "claimed"
        except svc.Conflict:
            result = "conflict"
        finally:
            worker.close()
        with guard:
            outcomes.append(result)

    threads = [threading.Thread(target=race, args=(i,)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    check("B4 exactly one owner from 10 claimants",
          outcomes.count("claimed") == 1 and svc.claim_count(conn, target) == 1,
          f"outcomes={outcomes}")

    # B5 - return closes the item
    svc.confirm_return(conn, third)
    try:
        svc.claim_item(conn, third, "u003")
        refused_again = False
    except svc.Conflict:
        refused_again = True
    check("B5 returned item cannot be claimed again",
          svc.get_item(conn, third)["status"] == "Returned" and refused_again)

    # quality: an unknown claimant is refused, not injected
    try:
        svc.claim_item(conn, second, "u002' OR 1=1 --")
        injected = True
    except svc.ServiceError:
        injected = False
    check("quality injection attempt refused", not injected and svc.claim_count(conn, second) == 0)

    check("quality claim invariant holds for every item",
          conn.execute(
              "SELECT COUNT(*) AS n FROM (SELECT item_id FROM claims GROUP BY item_id"
              " HAVING COUNT(*) > 1)"
          ).fetchone()["n"] == 0)

    conn.close()
    print(f"\n{len(PASSED)} checks passed")


if __name__ == "__main__":
    main()
