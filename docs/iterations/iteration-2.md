# Iteration 2 record

**Window:** Week 2, days 4-7 (four working days).
**Goal:** make the claim rule hold under contention, close the returned-item
state, and put the acceptance checks into CI so the defect from Iteration 1
cannot come back unnoticed.

## Items selected

| ID | User story | Acceptance criteria | Outcome |
| --- | --- | --- | --- |
| B4 | As a user I cannot claim the same item twice. | 10 simultaneous claims: 1 succeeds, 9 return HTTP 409. | Done |
| B5 | As a coordinator I confirm a handover. | The status becomes Returned and the item cannot be claimed again. | Done |
| B6 | As a team we run the tests on every change. | A pull request cannot be merged while the tests fail. | Done |

## Responsibilities

| Member | Work in this iteration |
| --- | --- |
| Hongyi Chu | `BEGIN IMMEDIATE` transaction and the 409 mapping in `claim_service.py` |
| Chenxin Jin | Error state in the item view, flash message for the lost race |
| Tong Zheng | `UNIQUE (item_id)` in `schema.sql`, pytest suite, CI workflow |
| Jingchen Mao | Coordinator return route and test, iteration record, measurements |

## The change that fixed B4

`claim_service.claim_item` now: opens `BEGIN IMMEDIATE`, reads the status inside
that transaction, inserts the claim, updates the status, commits. `claims` carries
`UNIQUE (item_id)`, so even if the read were stale the insert fails and the
transaction rolls back to a 409.

## How the increment is verified

- `pytest -q` - acceptance tests named after backlog ids, run in CI on every
  pull request (`.github/workflows/tests.yml`).
- `python scripts/smoke.py` - the same checks with no dependency beyond the
  standard library, 8 checks, 0.59 s.
- `python scripts/bench.py` - contention and latency measurement.
- `python scripts/naive_vs_fixed.py` - the defect and the fix compared on the
  same workload.

## Process improvement and its measurement

| Measure | Iteration 1 | Iteration 2 |
| --- | --- | --- |
| Backlog items with an executable check | 0 of 6 | 6 of 6 |
| Time to verify one change | manual click-through, minutes, not repeatable | `smoke.py` 0.59 s, `pytest` in CI |
| Double-claim rate, 20 trials of 10 claimants | 20/20 trials, up to 10 claims per item | 0/20 trials |
| Result attached to the review | a comment | a CI run plus a named acceptance test |

## Outcome and evidence

- All six backlog items pass their acceptance criteria
  (`pytest` output, `results/smoke.txt`).
- The invariant holds under 100 simultaneous attempts with no lock errors and no
  duplicate claim rows (`results/bench.json`).
- p95 of the claim path is 3.43 ms uncontended, 1304.87 ms under 5-way
  contention, where the wait is the write lock rather than work.

## Retrospective

What worked: writing the failing check first (the naive implementation fails the
B4 check every time, the fixed one passes) turned an argument about correctness
into a command anyone can run. CI removed the judgement call from the merge.

What did not: The suite is 11 checks for six items, so regressions inside a
scenario are still possible; and the contention test takes about a second per
trial, so it stays out of the fast loop and runs in CI only.

Next change the team would make: a PR template field that names the backlog id,
so traceability is enforced by the template rather than by reviewer memory.
