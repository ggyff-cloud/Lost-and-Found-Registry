"""Compare the naive claim check with the rule the product ships.

The naive version is what the endpoint looks like when "no two people can claim
the same item" is implemented as a read followed by a write, in two separate
statements, with no transaction and no database constraint. The fixed version is
:func:`claim_service.claim_item`.

Both are run under the same contention - ten claimants, one item, twenty trials -
so the report can quote a measured rate instead of an opinion:

    python scripts/naive_vs_fixed.py
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import claim_service as svc  # noqa: E402
import db as database  # noqa: E402

TRIALS = 20
CLAIMANTS = 10
RESULTS = ROOT / "results"

NAIVE_SCHEMA = """
CREATE TABLE users (id TEXT PRIMARY KEY, role TEXT NOT NULL);
CREATE TABLE items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Unclaimed'
);
CREATE TABLE claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    claimant_id TEXT NOT NULL
);
"""


def naive_claim(conn: sqlite3.Connection, item_id: int, claimant_id: str) -> str:
    """Claim without a transaction and without a constraint.

    Args:
        conn: Open connection.
        item_id: Item to claim.
        claimant_id: Claimant id.

    Returns:
        ``"claimed"`` or ``"refused"``.
    """
    row = conn.execute("SELECT status FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None or row[0] != "Unclaimed":
        return "refused"
    conn.execute("INSERT INTO claims (item_id, claimant_id) VALUES (?, ?)", (item_id, claimant_id))
    conn.execute("UPDATE items SET status = 'Claimed' WHERE id = ?", (item_id,))
    return "claimed"


def setup_naive(path: Path) -> None:
    """Create the naive database used by one trial.

    Args:
        path: File to create.
    """
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    conn.executescript(NAIVE_SCHEMA)
    conn.execute("INSERT INTO users VALUES ('u001','reporter')")
    rows = [(f"u9{i:02d}", "claimant") for i in range(CLAIMANTS)]
    conn.executemany("INSERT INTO users VALUES (?,?)", rows)
    conn.execute("INSERT INTO items (name) VALUES ('Phone')")
    conn.close()


def run_trial(impl: str, work: Path, trial: int) -> int:
    """Race ten claimants for one item and return the claim count.

    Args:
        impl: ``"naive"`` or ``"fixed"``.
        work: Directory for the trial database.
        trial: Trial number, used in the file name.

    Returns:
        Number of claim rows left for the item.
    """
    path = work / f"{impl}-{trial:02d}.sqlite3"
    if impl == "naive":
        setup_naive(path)
        item_id = 1
    else:
        if path.exists():
            path.unlink()
        conn = database.connect(path)
        database.init_db(conn)
        conn.execute("INSERT OR IGNORE INTO users (id, role) VALUES ('u001','reporter')")
        conn.executemany(
            "INSERT OR IGNORE INTO users (id, role) VALUES (?, 'claimant')",
            [(f"u9{i:02d}",) for i in range(CLAIMANTS)],
        )
        item_id = svc.register_item(
            conn, name="Phone", place="Lab", occurred_at="2026-11-20", reporter_id="u001"
        )
        conn.close()

    def worker(index: int) -> None:
        """Attempt the claim from its own connection."""
        conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
        try:
            if impl == "naive":
                naive_claim(conn, item_id, f"u9{index:02d}")
            else:
                svc.claim_item(conn, item_id, f"u9{index:02d}")
        except Exception:
            pass
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(CLAIMANTS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    conn = sqlite3.connect(path, timeout=10.0)
    count = conn.execute("SELECT COUNT(*) FROM claims WHERE item_id = ?", (item_id,)).fetchone()[0]
    conn.close()
    path.unlink(missing_ok=True)
    return int(count)


def main() -> None:
    """Run both implementations and write ``results/naive_vs_fixed.json``."""
    work = RESULTS / "trials"
    work.mkdir(parents=True, exist_ok=True)

    summary: dict[str, object] = {"trials": TRIALS, "claimants": CLAIMANTS}
    for impl in ("naive", "fixed"):
        counts = [run_trial(impl, work, trial) for trial in range(TRIALS)]
        summary[impl] = {
            "trials_with_double_claim": sum(1 for c in counts if c > 1),
            "max_claims": max(counts),
            "mean_claims": round(statistics.fmean(counts), 2),
            "counts": counts,
        }

    (RESULTS / "naive_vs_fixed.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    for impl in ("naive", "fixed"):
        row = summary[impl]
        print(f"{impl:<6} trials with a double claim: {row['trials_with_double_claim']}/{TRIALS}"
              f"  max {row['max_claims']}  mean {row['mean_claims']}")
    print(f"written to {RESULTS / 'naive_vs_fixed.json'}")


if __name__ == "__main__":
    main()
