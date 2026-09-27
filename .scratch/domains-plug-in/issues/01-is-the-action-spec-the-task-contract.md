Type: grilling
Status: open
Blocked by:

# The action contract under the spine

## Question

An action is now an **intent + a contract** (no graph). Decide the contract's
exact fields and how it relates to `the-task-contract`'s type-level
`TaskContract` (constraints, allowed_actions, approval_policy, budget,
acceptance_template): one object or two, and which name wins.

Candidate fields: name; recognition reasoning + examples (triage); allowed step
types; allowed toolsets; model tier(s); budget (steps / tokens / time / max
replans); acceptance criteria; approval policy; intake enricher and
`placement_identity`. Which overlap (toolsets ≈ allowed_actions), what replaces
`TaskTypeSpec.graph`, and where its dead fields (`extractor`, `needs`) go.
