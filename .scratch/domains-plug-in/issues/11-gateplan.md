Type: grilling
Status: open
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
