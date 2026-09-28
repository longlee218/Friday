Type: grilling
Status: resolved
Blocked by: 10, 11

# The WorkflowRunner and adaptive replan

## Question

Decide how the runner executes a frozen plan's phases durably (a phase memoized
by `(plan_version, phase_id)`, resume from the last incomplete phase). Within a
phase the agent adapts on its own; the Planner revisits only **between phases**
or when an agent reports its hypothesis was wrong. Decide how an agent signals
that, the obstacle ladder (transient → retry the phase; hypothesis wrong →
re-plan the remaining phases and re-gate; goal gone → abort with a "situation
changed" draft; over `max_replans` → `HandOver`), and how a re-plan reuses —
never re-runs — finished phases.

> Note from "The plan schema and the step vocabulary" (10): the Plan has no
> hypotheses and no per-case done-criteria; a step result is keyed by
> `(task_id, step_key)` (content hash), not `(plan_version, phase_id)`.

> Note from "The Planner" (12): a replan is a fresh Planner conversation given
> the latest intake context, the current plan, the stored results of finished
> steps and the replan reason; each replan gets its own 2 gate rewrites.

## Answer

Decided 2026-09-28 (grilling).

```
WorkflowRunner      core, plain code; walks the frozen plan's steps in order
for each step:
  result stored at (task_id, step_key)?  → skip, hand it to readers
  elapsed ≥ contract.limits.total_time?  → HandOver out_of_time
  run the step (one DBOS step)
    crashed / model error / timed out    → retry, core constant (2) → HandOver step_failed
    agent → its result shape             → store, next step
    agent → Ask | HandOver               → store, the plan stops there
    agent → Replan(reason, found)        → store, then:
              replans used = max_replans → HandOver replans_exhausted
              else                       → Planner (fresh conversation) → GatePlan
                                            → run plan v(n+1) from its first step
```

1. **An agent signals "wrong direction" with a core terminal tool
   `replan(reason, found)`**, given to every agent beside `ask_reporter` /
   `hand_over`. `found` is what it read that the next plan should know. The
   outcome is a `Replan`. No hypothesis ids (ticket 10 dropped hypotheses).
2. **The Planner is called only on a signal** — an agent's `Replan`, or a
   reply that changes `placement_identity` (ticket 14) — never as a check-in
   after every step. Plans are ≤ `max_steps` long and mostly `agent → draft`.
3. **Transient failure has two layers.** A tool error inside the agent loop
   is the agent's to handle (as today). A whole step failing (model API
   error, timeout, crash) → the runner retries it, a core constant (2), then
   `HandOver` `step_failed`. Neither the Planner nor `max_replans` is
   involved.
4. **Crash resume is per step, not per turn.** DBOS memoizes a step; an agent
   step caught mid-loop re-runs from its start (read-only, minutes). No
   per-turn `message_history` checkpoint.
5. **`max_replans` counts every replan** — an agent's `Replan` and a
   reply-driven one (placement changed). A gate refusal still does not count
   (ticket 11).
6. **A `Replan` is stored like every other result**, at `(task_id,
   step_key)`, with one reuse rule and no exception: a step with a stored
   result is skipped and its readers get that result; a reused `Replan` is
   data, never a new signal. So the new plan says what to do with it:
   - change direction: `p1' agent (new brief) → draft reads p1'`
   - build on it: `p1 (reused) → p1' agent reads p1 → draft reads p1'`
   - stop: `p1 (reused) → draft reads p1`
   No loop is possible (a stored step never re-runs) and GatePlan needs no
   new rule.
7. **No separate abort.** "Goal gone" is a replan whose plan ends in `draft`
   ("the situation changed, here is what was found") or `hand_over`. The
   Planner alone decides direction; the runner never tells "local" from
   "fundamental" — an agent can't either.
8. **Over `max_replans`** → `HandOver` `replans_exhausted` carrying every plan
   version, every stored result and the last `found`; operator only, like
   `planner_failed`.
9. **Time**: before each step and each retry the runner checks `elapsed ≥
   total_time` → `HandOver` `out_of_time`. It never cuts a running step; the
   agent's own budget bounds it.

`HandOver` reasons from code, all countable: `planner_failed` (12),
`step_failed`, `replans_exhausted`, `out_of_time`.

Carried: to **14** — an agent step ending in `Ask` is stored at its
`step_key`, so after the reply that stored `Ask` must be dropped (or the step
keyed apart) for the step to re-run and see the reply; how is 14's call. A
reply-driven replan counts toward `max_replans`.
