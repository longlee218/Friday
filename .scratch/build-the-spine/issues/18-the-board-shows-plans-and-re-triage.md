Status: ready-for-agent
Blocked by: 17

# The board shows plans and re-triage

Decisions: [GatePlan](../../domains-plug-in/issues/11-gateplan.md) §6,
[Re-triage](../../domains-plug-in/issues/16-hand-off-between-actions-by-re-triage.md) §9,
[The Planner](../../domains-plug-in/issues/12-the-planner.md) §9.

## Goal

Read-only, within the map's web scope (no redesign):

- Task card: every plan version (steps, hash, gate errors of refused ones);
  code-authored `HandOver` reasons (`planner_failed`, `step_failed`,
  `replans_exhausted`, `asks_exhausted`, `retriages_exhausted`) told apart
  from a Planner-chosen `hand_over`.
- Timeline line "re-triaged `X` → `Y` — `<reason>`", `found` on expand; old
  plan collapsed.
- Approval card: one line of the plan that ran, e.g.
  `v2 · diagnose[logs, code] → draft`.
- `core.shell` refusals listed.

## Acceptance

- [ ] API tests for the new reads; `npm run build` and the axe/bundle gates pass.
- [ ] Seen once in the running board.
- [ ] Whole suite green; `code-review` done.
