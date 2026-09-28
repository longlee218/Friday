Status: ready-for-agent
Blocked by: 16

# Re-triage

Decision: [Hand-off between actions by re-triage](../../domains-plug-in/issues/16-hand-off-between-actions-by-re-triage.md).

## Goal

- A pass step before `deliver`: on a stored `Retriage`, triage the whole
  conversation + retriage note (`from`, `reason`, `found`); labels =
  actions − tried, no `skip`; tried actions listed under "Already tried".
- Bound `len(actions) - 1`, computed at boot → `HandOver retriages_exhausted`.
- Confident → `tasks.type` = new action, `pass_no + 1`, pending; replans
  reset, asks keep counting; re-triage count on the task. Low → `needs_human`
  with the note on the card. No second acknowledge.
- `evals/triage.jsonl` rows may carry `retriage_note` + `tried`; synthetic
  rows both ways + one low-confidence gold.

## Acceptance

- [ ] X → Y → X impossible (test); exhaustion tested.
- [ ] Crash during the re-triage step never triages twice.
- [ ] `run_triage_eval` with the new rows reported.
- [ ] `CONTEXT.md`: *re-triage*, *retriage note*.
- [ ] Whole suite green; `code-review` done.
