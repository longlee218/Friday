Type: grilling
Status: open
Blocked by: 01, 10

# GatePlan — what a plan must pass before it runs

## Question

Decide the gate's checks and their order: schema-valid against the vocabulary;
every step within the **action's contract** (allowed step types, toolsets); no
capability the run's scope does not grant; sensitivity / egress computed from
the plan; step count and estimated budget within the contract; freeze + hash.
What a failed gate does (re-plan how many times, then `HandOver`), and whether
the operator sees the frozen plan on the board / approval card.
