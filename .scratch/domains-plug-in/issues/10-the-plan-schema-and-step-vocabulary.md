Type: prototype
Status: open
Blocked by:

# The plan schema and the step-type vocabulary

## Question

The first thing to nail (durable-spine §13): the `Plan` schema (goal, budget,
on_obstacle, versioned steps with ids, inputs-from, needs) and the **closed
step-type vocabulary** — each type's input, output, `needs` and side-effect
class. Starting set to react to: `read_source`, `call_tool`, `sub_agent`,
`gather`, `branch`, `assert`, `ask` / `hand_over`, `draft`, `write_memory`
(→ candidate). Which are core, which a plugin may add, and how a step type is
registered. Stub it in `sdk` shape with one worked plan.
