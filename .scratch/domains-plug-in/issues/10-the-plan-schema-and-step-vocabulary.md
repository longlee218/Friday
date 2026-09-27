Type: prototype
Status: open
Blocked by:

# The plan schema and the step vocabulary

## Question

Stub the `Plan` as decided in the action-contract ticket: a **main-flow plan**,
not a program — goal, opening hypotheses, what to check first, done-criteria
(per-case acceptance), and ordered **phases**, each owned by a named agent with
the toolsets it is granted and the phases it reads from. The step vocabulary is
small and core-owned: `agent`, `ask`, `hand_over`, `draft`.

Decide each step type's fields and output, how a phase's result feeds the next,
how the plan carries its `ActionContract`, versioning (`plan_version`) and what
gets frozen + hashed. Show one worked plan for `backend.trace_problem`.
