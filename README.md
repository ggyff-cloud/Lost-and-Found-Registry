# Lost & Found Claim Registry

Course project for Assignment 2: a student reports a lost item, another student
claims it, and a coordinator confirms the handover. An item can be claimed by
exactly one student, and that rule survives two people claiming at the same
instant.

Stack: Python 3.12, Flask, SQLite. No ORM, no external service.

## Setup

```sh
python -m venv .venv
.venv\Scripts\activate        # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
```

## Run

```sh
python app.py
```

Open <http://127.0.0.1:5000/>. The database `lostfound.sqlite3` is created and
seeded with synthetic users on first start (`u001` reporter, `u002`/`u003`
claimants, `u900` coordinator). Set `LOSTFOUND_DB` to point at another file.

## Test

```sh
pytest -q                     # acceptance tests, one per backlog item
python scripts/smoke.py       # the same checks without pytest installed
python scripts/bench.py       # concurrency and latency measurements
python scripts/naive_vs_fixed.py   # the claim rule, with and without the fix
```

`pytest` covers the Flask routes through the test client; the check and measurement scripts
import the same service functions directly, so they run on the standard library
alone.

## Layout

```
app.py               Flask routes, HTML flow and JSON API
claim_service.py     the claim rules: one transaction, one UNIQUE constraint
db.py                connection pragmas, schema setup, synthetic seed
schema.sql           tables; claims carries UNIQUE(item_id)
templates/           four templates for the HTML flow
tests/               pytest acceptance tests, mapped to backlog ids B1-B6
scripts/             measurements that need only the standard library
results/             generated numbers quoted in the report
docs/iterations/     the record kept for each iteration
.github/workflows/   CI job that runs pytest on every pull request
```

## Where the rule is enforced

| Rule | Enforced by |
| --- | --- |
| One claim per item | `UNIQUE (item_id)` in `schema.sql` |
| Check and write are atomic | `BEGIN IMMEDIATE` in `claim_service.claim_item` |
| A returned item stays closed | `status` check inside the same transaction |
| Callers cannot inject SQL | parameterised statements only; ids are foreign keys |

The browser never decides availability. Disabling a button only stops the
honest user; the database refuses the second writer either way.

## Measured behaviour

Recorded on Windows 11, Python 3.12.14, SQLite 3.53.1 (`results/bench.json`):

| Scenario | Result |
| --- | --- |
| 20 items, 5 claimants each, WAL | 20 claims, 80 conflicts, 0 lock errors, 0 invariant violations |
| 20 items, 5 claimants each, rollback journal | same outcome, 1.26x slower wall time |
| 200 sequential claims | p95 3.43 ms |
| naive check-then-insert, 20 trials | 20/20 trials produced a double claim (up to 10) |
| shipped implementation, 20 trials | 0/20 produced a double claim |

## Known limitations

- No authentication: the caller states who it is, so the demo proves the claim
  invariant rather than authorisation.
- One process, one database file. SQLite serialises writers, which is what makes
  the fix small and honest for this scale.
- The list is not paginated; the backlog stops at six items by design.
