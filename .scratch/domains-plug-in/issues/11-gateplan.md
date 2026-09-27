Type: grilling
Status: resolved
Blocked by: 10

# GatePlan — what a plan must pass before it runs

## Question

Decide the gate's checks and their order against the `ActionContract`: schema
valid; every phase's agent in `allowed_agents`; every granted toolset in
`allowed_toolsets` and within the agent's own maximum; step types in
`allowed_step_types`; sensitivity / egress computed from the toolsets the plan
grants; freeze + hash.

Budget lives on the agent only, so a plan's total cost is the sum over its
phases — decide whether the contract also caps the **number of phases**. What a
failed gate does (re-plan how many times, then `HandOver`), and whether the
operator sees the frozen plan on the board / approval card.

## Answer

Decided 2026-09-28 (grilling).

GatePlan is plain code, no model call, run on every new plan version (v1 and
every replan). Checks, in order:

```
1. schema + shape   ticket 10's rules (ids unique, one terminal step last,
                    reads earlier only, draft reads something)
                    → fails: stop here, return this error alone
2. contract         (all errors gathered)
                    step type ∈ allowed_step_types
                    agent     ∈ allowed_agents
                    each agent step's toolsets ⊆ contract.allowed_toolsets
                                                 ∩ agent.max_toolsets
                    → outside: REFUSE, never clip
3. limits           (all errors gathered)
                    len(steps) ≤ contract.limits.max_steps
                    Σ time budget of steps with no stored result
                        ≤ contract.limits.total_time − time already used
4. freeze + hash    (ticket 10: sha256 of the canonical plan)
```

1. **Sensitivity / egress: deferred.** Nothing to compute today — every run is
   `internal`, one provider, no toolset is `egress`, and the plan cannot exceed
   the contract ceiling the plugin author wrote. When the trigger lands (the
   first `egress` toolset, or personal/restricted data — DESIGN-v2 §9.2), the
   check goes **in the gate**, the one place that sees the whole plan first.
2. **`max_steps` added to `contract.limits`** — *amends ticket 01*: limits =
   total time · max replans · max steps (e.g. `trace_problem`: 3). Budget on
   the agent bounds time, not tokens; `max_steps` bounds the plan's length.
3. **The time check turns "timed out mid-run" into "refused at the gate"**,
   with a reason the Planner can act on.
4. **A replan is checked against the time left**: only steps with no result
   at `(task_id, step_key)` count, against `total_time − elapsed`. `max_steps`
   counts the whole plan, reused steps included.
5. **A refusal** returns every error (after schema passes) as text to the
   Planner. Rewrites are a **core constant (2)**, the same for every action,
   not configurable, and **not counted in `max_replans`** — that counts
   changes of direction while running; a refusal is the Planner breaking a
   rule before anything ran. Past the constant → `HandOver` carrying the last
   plan and its errors.
6. **The operator sees, never approves, the plan.** Every step reads inside
   the contract; the only thing leaving Friday is the `Reply`, which already
   waits. Board: every plan version on the task card (steps, hash, gate errors
   of refused versions), read-only. Approval card: one line of the plan that
   ran, e.g. `v2 · diagnose[logs, code] → draft`.

Carried: to **12** — how the Planner uses the gate's errors to rewrite. To
**14** — where plans and refused versions are stored (the board reads them).
