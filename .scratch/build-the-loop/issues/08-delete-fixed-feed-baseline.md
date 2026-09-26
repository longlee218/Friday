Status: ready-for-human
Blocked by: 07

# Delete the fixed-feed baseline (diagnose_reads=False)

Decision: [A deterministic eval for a loop that reads what it likes](../../the-graph-becomes-a-loop/issues/02-a-deterministic-eval-for-a-loop-that-reads-what-it-likes.md)
(Q7 — delete, gated on the eval showing no regression).

## Code half done by ticket 5 (option B, 2026-09-26)

Under option B ([[build-the-loop-scoring-deferred]]) removing the two fixed nodes
(ticket 5) already deleted the `diagnose_reads=False` path and the flag — leaving
them orphaned that change. So the **code** half of this ticket landed with ticket
5 (acceptance boxes 2 and 3 below). What remains is only the **measurement** (box
1), which needs ≥10 real captured cases and a model — parked with ticket 7 until
Friday launches. `Blocked by` reduced `06, 07` → `07` (the code no longer waits
on the rewire). This becomes a *post-launch* verification: run `run_api_issue_eval`
and confirm the agentic loop ties/beats what the deleted baseline would have said.

## Goal

Remove the `diagnose_reads=False` path so the agentic loop is the only mode —
**only after** the eval proves it does not regress.

- Prove agentic **≥ baseline** on the captured set (≥ 10 cases, ticket 07),
  numbers reported (accuracy per-case, `run_api_issue_eval`).
- Delete the baseline feed path and the `diagnose_reads` config flag; remove
  code only this change orphans.

## Acceptance

- [ ] Reported numbers show agentic ties/beats baseline on every captured case.
      **(PARKED — needs real cases + model; option B, post-launch.)**
- [x] `diagnose_reads=False` path and flag gone; no orphans. (Done by ticket 5.)
- [x] Whole suite green; `code-review` done. (Ticket 5: 1580 passed / 7 known env
      fails; code-review clean, no gate lost.)
