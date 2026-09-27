Type: grilling
Status: open
Blocked by:

# Renaming the actions and relabelling history

## Question

The new names reach stored data: `tasks.type`, `messages.decision_type`,
`node_runs.dag_name`, `dag_state.dag_name`, memory kinds `devops.*` → `backend.*`,
agent `devops.diagnose`, `evals/triage.jsonl` (23 `devops.api_issue`, 4
`docs.doc_question`, 4 `access_request`), ~38 test files, `web/` (`Tag.tsx`
hard-codes the type list). `devops.api_issue` **splits in two**, so an old row
cannot be renamed mechanically. Decide: an Alembic data migration (the earlier
renames' pattern) with what mapping for the split — or relabel the eval set by
hand and leave old task rows under a legacy name? And the web reads the action
list from the API.
