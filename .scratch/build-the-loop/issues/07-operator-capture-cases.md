Status: ready-for-human
Blocked by: 01

# Operator: capture ≥ 10 cases

Decision: [A deterministic eval for a loop that reads what it likes](../../the-graph-becomes-a-loop/issues/02-a-deterministic-eval-for-a-loop-that-reads-what-it-likes.md)
(Q5 — the real gate for deleting the baseline is case count, not the mechanism).

## Goal (operator-driven — a `task`, not a decision)

Grow `data/cases/` to ≥ ~10 captured cases in the superset format (ticket 01),
each with operator-confirmed labels (`decisive`, `cause`, `cause_mentions`,
`conclusive`). Friday proposes the four; the operator confirms/corrects — the
true cause of a production incident is a fact about their system, not the
model's to assert.

Until this lands, `run_api_issue_eval` prints "regression check, not a score"
and ticket 08 stays blocked.

## Acceptance

- [ ] ≥ ~10 cases in `data/cases/`, superset format, labelled and confirmed.
- [ ] Cases read like real traffic (Vietnamese, pasted stack traces, curls),
      per `evals/README.md`'s coverage notes.
- [ ] `run_api_issue_eval` reports a score (≥ 10 cases), not a bare regression
      check.
