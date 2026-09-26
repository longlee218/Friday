Type: prototype
Status: open
Blocked by: 01

# The merged Contract/Plan schema

## Question

Specify, as a stub to react to, the **one** artifact that is both the contract
and the Plan — the hybrid two-part shape:

- **type-level** (operator-authored once per task type): `constraints`,
  `allowedActions`, `approvalPolicy`, `budget { max_steps, max_tokens,
  max_time }`. These map to today's grounding gate, tool `needs`, outbox
  approval, and `MAX_READS`/token/timeout — the ticket names each source it
  replaces.
- **instance-level** (per case): `objective`, `acceptanceCriteria`
  (model-proposed → operator-confirmed), and the `steps` (fixed today; a
  `Planner`'s output under durable-spine).

Show how it maps onto the user's `TaskContract` sketch (objective / constraints
/ acceptanceCriteria / allowedActions / approvalPolicy / budget) **plus** the
durable-spine `Plan` (goal / steps / on_obstacle) — since they are one artifact.
Decide field names against `CONTEXT.md` vocabulary; add the new term(s) there.

Blocked by ticket 01: whether it is a static per-type object now or the full
dynamic Plan decides which fields are inherited vs generated.
