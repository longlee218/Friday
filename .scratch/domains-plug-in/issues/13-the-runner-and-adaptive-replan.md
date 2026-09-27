Type: grilling
Status: open
Blocked by: 10, 11

# The WorkflowRunner and adaptive replan

## Question

Decide how the runner executes a frozen plan durably (a step memoized by
`(plan_version, step_id)`, resume from the last incomplete step), what it checks
after each step, and the obstacle ladder — transient → retry (bounded); local →
PATCH the tail and re-gate; fundamental → ABORT with a "situation changed"
draft; ambiguous or over `max_replans` → `HandOver`. Where the bound sits and
how a replan reuses (never re-runs) finished steps.
