"""Reproducible measurements for the quality evaluation in the report.

Runs on the standard library only, so the numbers can be regenerated on any
machine that can run Python 3.12 - no Flask, no network, no test server:

    python scripts/bench.py

It writes ``results/bench.json`` and prints a short table. Three things are
measured: the claim invariant under real contention, the latency of the claim
path, and the difference between write-ahead logging and the default rollback
journal for the same workload.
"""

from __future__ import annotations

import json
import platform
import sqlite3
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import claim_service as svc  # noqa: E402
import db as database  # noqa: E402

RESULTS = ROOT / "results"
ITEMS_PER_SCENARIO = 20
CLAIMANTS = ("u002", "u003", "u004", "u005", "u006")
SEQUENTIAL_CLAIMS = 200


def fresh_db(path: Path, *, wal: bool) -> sqlite3.Connection:
    """Create a database with the schema, the seed users and nothing else.

    Args:
        path: File to create; an existing file is deleted first.
        wal: Whether to enable write-ahead logging for this file.

    Returns:
        A connection configured like the application's.
    """
    if path.exists():
        path.unlink()
    conn = database.connect(path, wal=wal)
    database.init_db(conn)
    conn.executemany(
        "INSERT OR IGNORE INTO users (id, role) VALUES (?, ?)",
        [("u001", "reporter"), ("u900", "coordinator")]
        + [(cid, "claimant") for cid in CLAIMANTS],
    )
    return conn


def add_items(conn: sqlite3.Connection, count: int) -> list[int]:
    """Insert ``count`` items and return their ids.

    Args:
        conn: Open connection.
        count: How many items to create.

    Returns:
        The new item ids in insertion order.
    """
    return [
        svc.register_item(
            conn,
            name=f"Item {n:03d}",
            place="Library",
            occurred_at="2026-11-20",
            reporter_id="u001",
        )
        for n in range(count)
    ]


def percentiles(samples: list[float]) -> dict[str, float]:
    """Summarise a latency list in milliseconds.

    Args:
        samples: Durations in seconds.

    Returns:
        Mean, p50, p95, p99 and max in milliseconds, rounded to two decimals.
    """
    if not samples:
        return {}
    ordered = sorted(samples)
    def pick(q: float) -> float:
        index = min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))
        return ordered[index]
    return {
        "mean_ms": round(statistics.fmean(ordered) * 1000, 2),
        "p50_ms": round(pick(0.50) * 1000, 2),
        "p95_ms": round(pick(0.95) * 1000, 2),
        "p99_ms": round(pick(0.99) * 1000, 2),
        "max_ms": round(ordered[-1] * 1000, 2),
    }


def contention_scenario(db_path: Path, *, wal: bool) -> dict[str, object]:
    """Have five claimants race for each of twenty items.

    Args:
        db_path: Database file created for this scenario.
        wal: Whether the file uses write-ahead logging.

    Returns:
        Success and refusal counts, wall time, throughput and latency stats.
    """
    setup = fresh_db(db_path, wal=wal)
    item_ids = add_items(setup, ITEMS_PER_SCENARIO)
    setup.close()

    latencies: list[float] = []
    outcomes: list[str] = []
    guard = threading.Lock()

    def worker(item_id: int, claimant: str) -> None:
        """Attempt one claim on its own connection, as a separate request would."""
        conn = database.connect(db_path, wal=wal)
        started = time.perf_counter()
        try:
            svc.claim_item(conn, item_id, claimant)
            result = "claimed"
        except svc.Conflict:
            result = "conflict"
        except sqlite3.OperationalError as exc:
            result = "locked" if "locked" in str(exc) else "error"
        finally:
            elapsed = time.perf_counter() - started
            conn.close()
        with guard:
            latencies.append(elapsed)
            outcomes.append(result)

    threads = [
        threading.Thread(target=worker, args=(item_id, CLAIMANTS[index % len(CLAIMANTS)]))
        for item_id in item_ids
        for index in range(5)
    ]
    started_all = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall = time.perf_counter() - started_all

    verify = database.connect(db_path, wal=wal)
    duplicate_items = verify.execute(
        "SELECT COUNT(*) AS n FROM (SELECT item_id FROM claims GROUP BY item_id HAVING COUNT(*) > 1)"
    ).fetchone()["n"]
    wrong_status = verify.execute(
        "SELECT COUNT(*) AS n FROM items i JOIN claims c ON c.item_id = i.id WHERE i.status != 'Claimed'"
    ).fetchone()["n"]
    verify.close()

    return {
        "wal": wal,
        "attempts": len(threads),
        "items": ITEMS_PER_SCENARIO,
        "claimed": outcomes.count("claimed"),
        "conflict": outcomes.count("conflict"),
        "locked": outcomes.count("locked"),
        "errors": outcomes.count("error"),
        "wall_s": round(wall, 3),
        "attempts_per_s": round(len(threads) / wall, 1),
        "invariant_violations": int(duplicate_items),
        "status_mismatches": int(wrong_status),
        **percentiles(latencies),
    }


def sequential_scenario(db_path: Path) -> dict[str, object]:
    """Claim items one at a time to get an uncontended latency baseline.

    Args:
        db_path: Database file created for this scenario.

    Returns:
        Latency stats for the claim and the registration path.
    """
    conn = fresh_db(db_path, wal=True)
    item_ids = add_items(conn, SEQUENTIAL_CLAIMS)

    claim_latencies: list[float] = []
    for index, item_id in enumerate(item_ids):
        started = time.perf_counter()
        svc.claim_item(conn, item_id, CLAIMANTS[index % len(CLAIMANTS)])
        claim_latencies.append(time.perf_counter() - started)

    register_latencies: list[float] = []
    for n in range(SEQUENTIAL_CLAIMS):
        started = time.perf_counter()
        svc.register_item(
            conn, name=f"Extra {n}", place="Canteen", occurred_at="2026-11-20", reporter_id="u001"
        )
        register_latencies.append(time.perf_counter() - started)

    conn.close()
    return {
        "claims": SEQUENTIAL_CLAIMS,
        "claim": percentiles(claim_latencies),
        "register": percentiles(register_latencies),
    }


def injection_probe(db_path: Path) -> dict[str, object]:
    """Send SQL fragment inputs through the normal code path.

    Two payloads are used: one as the claimant id (a foreign key), one as the
    item name (free text that is displayed back). Neither should do anything
    except appear as data.

    Args:
        db_path: Database file created for this probe.

    Returns:
        What the service did with each payload, and whether the schema survived.
    """
    conn = fresh_db(db_path, wal=True)
    item_id = add_items(conn, 1)[0]

    id_payload = "u002' OR 1=1 --"
    id_status: str
    try:
        svc.claim_item(conn, item_id, id_payload)
        id_status = "accepted"
    except svc.ServiceError as exc:
        id_status = f"refused-{exc.status}"
    claims_after = conn.execute("SELECT COUNT(*) AS n FROM claims").fetchone()["n"]

    name_payload = "Umbrella'; DROP TABLE items; --"
    inserted = svc.register_item(
        conn, name=name_payload, place="Library", occurred_at="2026-11-20", reporter_id="u001"
    )
    stored_name = conn.execute("SELECT name FROM items WHERE id = ?", (inserted,)).fetchone()["name"]
    items_alive = conn.execute(
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name='items'"
    ).fetchone()["n"]
    conn.close()
    return {
        "id_payload": id_payload,
        "id_outcome": id_status,
        "claims_injected": int(claims_after),
        "name_payload": name_payload,
        "name_stored_verbatim": stored_name == name_payload,
        "items_table_survived": items_alive == 1,
    }


def main() -> None:
    """Run every scenario and write the results file."""
    RESULTS.mkdir(exist_ok=True)
    wal = contention_scenario(RESULTS / "bench_wal.sqlite3", wal=True)
    rollback = contention_scenario(RESULTS / "bench_rollback.sqlite3", wal=False)
    sequential = sequential_scenario(RESULTS / "bench_sequential.sqlite3")
    injection = injection_probe(RESULTS / "bench_injection.sqlite3")

    payload = {
        "environment": {
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "platform": platform.platform(),
        },
        "contention_wal": wal,
        "contention_rollback": rollback,
        "sequential": sequential,
        "injection": injection,
    }
    (RESULTS / "bench.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"python {platform.python_version()} / sqlite {sqlite3.sqlite_version}")
    header = f"{'scenario':<22}{'claimed':>9}{'conflict':>10}{'locked':>8}{'p95 ms':>9}{'wall s':>8}"
    print(header)
    print("-" * len(header))
    for name, row in (("20x5 claims, WAL", wal), ("20x5 claims, rollback", rollback)):
        print(f"{name:<22}{row['claimed']:>9}{row['conflict']:>10}{row['locked']:>8}"
              f"{row['p95_ms']:>9}{row['wall_s']:>8}")
    print(f"{'200 sequential claims':<22}{'':>9}{'':>10}{'':>8}{sequential['claim']['p95_ms']:>9}")
    print(f"invariant violations: {wal['invariant_violations']} / {rollback['invariant_violations']}")
    print(f"injection: id {injection['id_outcome']}, rows {injection['claims_injected']}, "
          f"name verbatim {injection['name_stored_verbatim']}, schema intact {injection['items_table_survived']}")
    print(f"written to {RESULTS / 'bench.json'}")


if __name__ == "__main__":
    main()
