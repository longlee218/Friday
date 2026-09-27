Type: grilling
Status: open
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
