Type: grilling
Status: open
Blocked by: 10

# The Planner

## Question

Decide the Planner, which writes the main-flow plan (goal, hypotheses, what to
check first, done-criteria, phases → agents): what it is given (intake context,
the action's contract and its allowed agents' descriptions; exemplars later),
its model tier and budget, how the operator customises it by prompt/declaration
(per action? per domain?), and its failure mode — no valid plan after N tries →
`HandOver`, with what shown. One Planner for every action, or may an action ship
planner guidance? And for a one-phase case, does the Planner still run?

> Note from "The plan schema and the step vocabulary" (10): the Plan has no
> hypotheses and no per-case done-criteria; a step result is keyed by
> `(task_id, step_key)` (content hash), not `(plan_version, phase_id)`.

> Note from "GatePlan" (11): a refused plan comes back with every gate error
> as text; the Planner gets 2 rewrites (core constant, not `max_replans`),
> then `HandOver` with the last plan + errors. How it uses the errors is this
> ticket's call. A replan's time is checked against the time left.
