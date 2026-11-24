# Iteration 1 record

**Window:** Week 2, days 1-3 (three working days).
**Goal:** deliver the happy path end to end, so a reporter can register an item
and a claimant can claim it, and demonstrate that two simultaneous claims still
produce two owners.
**Approach:** iterative and incremental, as chosen in Assignment 1.

## Items selected

| ID | User story | Acceptance criteria | Outcome |
| --- | --- | --- | --- |
| B1 | As a reporter I register a lost item. | The item is saved and appears in the list. | Done |
| B2 | As a claimant I see unclaimed items. | Only unclaimed items are listed. | Done |
| B3 | As a claimant I claim an item. | The claim is saved and the status becomes Claimed. | Done |
| B4 | As a user I cannot claim the same item twice. | 10 simultaneous claims: 1 succeeds, 9 return HTTP 409. | **Failed in this iteration** |

## Responsibilities

| Member | Work in this iteration |
| --- | --- |
| Hongyi Chu | Claim endpoint, item registration service, transaction boundary |
| Chenxin Jin | Registration form and the unclaimed list view |
| Tong Zheng | Schema, seed data, manual click-through script |
| Jingchen Mao | Coordinator return step (started), iteration record |

## How the increment was verified

By hand: start `python app.py`, register an item in one browser window, then
refresh and claim it. Two windows were opened to try to reproduce a double claim,
which failed to reproduce it - the request of the second window was sent after
the first had already committed, so the race never happened.

No executable check existed for any backlog item at the end of this iteration.

## Outcome and evidence

- Increment: register, list and claim work end to end; the claim rule does **not**
  hold under real contention.
- Defect found after the review, not before it: with ten simultaneous requests,
  the naive endpoint let **all ten** claim the same item
  (`results/naive_vs_fixed.json`, 20/20 trials, up to 10 claims per item).
- Root cause: a read followed by a separate write, no transaction, and no
  database constraint - nothing serialised the two writers.

## Retrospective

What worked: small vertical slices, one owner per view, the list and the form
were finished inside a day.

What did not: the only acceptance check was a person clicking, and clicking
cannot produce a race. The team reviewed the code by reading it, so a defect
that only appears under concurrency was invisible to review and to the demo.
Three concrete complaints were recorded:

1. A change is verified by hand, so the result is not reproducible and costs
   minutes per change.
2. There is no written mapping from a backlog item to evidence.
3. Reviews record an opinion, not a check that ran.

## Action carried into Iteration 2

Write an executable acceptance check per backlog item, run it on every pull
request in CI, and require a green run before merging (see
`docs/iterations/iteration-2.md` and `scripts/smoke.py`).
