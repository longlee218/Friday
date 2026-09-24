Status: ready-for-agent
Blocked by: 06, 07

# Delete the fixed-feed baseline (diagnose_reads=False)

Decision: [A deterministic eval for a loop that reads what it likes](../../the-graph-becomes-a-loop/issues/02-a-deterministic-eval-for-a-loop-that-reads-what-it-likes.md)
(Q7 — delete, gated on the eval showing no regression).

## Goal

Remove the `diagnose_reads=False` path so the agentic loop is the only mode —
**only after** the eval proves it does not regress.

- Prove agentic **≥ baseline** on the captured set (≥ 10 cases, ticket 07),
  numbers reported (accuracy per-case, `run_api_issue_eval`).
- Delete the baseline feed path and the `diagnose_reads` config flag; remove
  code only this change orphans.

## Acceptance

- [ ] Reported numbers show agentic ties/beats baseline on every captured case.
- [ ] `diagnose_reads=False` path and flag gone; no orphans.
- [ ] Whole suite green; `code-review` done.
