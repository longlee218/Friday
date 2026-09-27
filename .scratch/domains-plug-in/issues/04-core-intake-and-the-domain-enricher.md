Type: prototype
Status: open
Blocked by: 01

# Core intake and the domain enricher

## Question

Stub the split of today's `plugins/devops/graph/intake.py`: the **core intake
context** every action gets (request text, reported-at, uuid/artifact hints,
memory/skill retrieval) and the **domain enrichment** (backend: `Placement` from
the environment/service/project rows). Where `placement_identity` is declared per
domain, what `ops.request_permission` gets (no placement), and how the enriched
context reaches the action's agent. Must keep the reply-resume path working
(intake re-runs every pass over the whole conversation).
