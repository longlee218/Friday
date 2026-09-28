Status: ready-for-agent
Blocked by: 05

# Plan, `step_key` and GatePlan

Decisions: [The plan schema and the step vocabulary](../../domains-plug-in/issues/10-the-plan-schema-and-step-vocabulary.md),
[GatePlan](../../domains-plug-in/issues/11-gateplan.md) (as amended by 17).
Stub: `plan_schema_and_step_vocabulary_STUB.py` on its prototype branch.

## Goal

Plain code in `friday/kernel/spine/plan.py` and `plan_gate.py`, not wired yet.

- `Plan(task_id, action, plan_version, replaces, contract, goal, steps)`;
  steps `agent(id, agent, toolsets, brief, reads)` / `ask(id, question,
  reads)` / `hand_over(id, reason, reads)` / `draft(id, reads)`.
  `Ask`/`Reply`/`HandOver` and the `Outcome` union live beside it
  (`sdk/actions.py` moves here).
- `plan_hash` = sha256 of canonical JSON of the whole plan.
- `step_key` = hash(step fields minus `id`/`reads` + keys of the steps it
  reads + `placement_identity` — ticket 14 §5).
- GatePlan: 1 schema + shape (stop on fail) → 2 contract (gather; refuse,
  never clip; toolsets ⊆ contract ∩ agent) → 3 limits (`max_steps`) →
  4 freeze + hash. No time check. Sensitivity/egress deferred.

## Acceptance

- [ ] Shape rules each refused by a test (dup ids, terminal not last,
      forward `reads`, `draft` reading nothing).
- [ ] A contract breach returns all errors, never a clipped plan.
- [ ] Same step content → same `step_key` across versions; a changed
      placement → a different key.
- [ ] `CONTEXT.md` § Vocabulary: *plan*, *step*, *step key*, *GatePlan*.
- [ ] Whole suite green; `code-review` done.
