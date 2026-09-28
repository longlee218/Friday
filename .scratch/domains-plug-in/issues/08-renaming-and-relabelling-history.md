Type: grilling
Status: resolved
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

## Carried in from ticket 05 (2026-09-28)

- The same Alembic migration also **drops `tasks.params`** (decided in
  "What replaces `params`"); no data is carried over.

## Answer

Resolved 2026-09-28 (grilling).

- **No data migration — wipe and rename in one go.** Friday has never run for
  real (live DB: 7 tasks, all `handled_by_operator`, none in flight; all 22
  `devops.api_issue` messages are "trace this failure"), so the split needs no
  row-by-row mapping. At build time: back up to `data/friday.db.bak-pre-<ticket>`,
  delete the file, `alembic upgrade head` on an empty DB. The existing chain
  stays (old names in old migrations are history); one **schema-only**
  migration drops `tasks.params` (from 05). No squash.
- **The names, all at once, no legacy alias in the registry:**
  `devops.api_issue` → `backend.trace_problem`, `docs.doc_question` →
  `backend.answer_question`, `access_request` → `ops.request_permission`,
  `devops.<kind>` → `backend.<kind>`, `devops.diagnose` → `backend.diagnose`.
- **Operator memory** (10 rows today) is reloaded with
  `import_context_files.py` under the `backend.*` kinds.
- **`evals/triage.jsonl`** is relabelled by hand, one to one: all 23
  `devops.api_issue` → `backend.trace_problem` (the operator's labels kept,
  including three that read like "how does it work"), 4 `docs.doc_question` →
  `backend.answer_question`, 4 `access_request` → `ops.request_permission`. No
  reclassification here — the trace/answer boundary examples are ticket 06's.
- **Web**: the API returns `[{name, domain}]`; the tag colour is per **domain**
  (one CSS class per domain, neutral for an unknown one); `skip`/`unknown` stay
  core tags. The hard-coded list in `web/src/ui/Tag.tsx` and the per-type
  classes in `index.css` go.
- Tests (~38 files) rename in the same commit as the code. Whether
  `dag_state`/`node_runs` survive is ticket 14's call — empty after the wipe
  either way.
