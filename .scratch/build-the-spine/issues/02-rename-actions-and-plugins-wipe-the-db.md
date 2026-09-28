Status: ready-for-agent
Blocked by: 01

# Rename actions and plugins, wipe the db

Decision: [Renaming the actions and relabelling history](../../domains-plug-in/issues/08-renaming-and-relabelling-history.md).
Runs on the old DAG machinery; only names and packages change.

## Goal

- Names, all at once, no legacy alias: `devops.api_issue` →
  `backend.trace_problem`, `docs.doc_question` → `backend.answer_question`,
  `access_request` → `ops.request_permission`, `devops.<kind>` →
  `backend.<kind>`, `devops.diagnose` → `backend.diagnose`,
  `devops.project` → `backend.project`.
- Packages: `plugins/devops` → `plugins/backend`; `plugins/docs` folds into
  `plugins/backend`; the core `access_request` type moves to a new
  `plugins/ops` (the kernel keeps no task type of its own besides `skip`).
- DB: back up to `data/friday.db.bak-pre-build-the-spine-02`, delete,
  `alembic upgrade head` on empty. Old migrations untouched. **Ask the
  operator before the wipe.**
- Operator memory re-imported under `backend.*` with `import_context_files.py`.
- `evals/triage.jsonl` relabelled 1:1 by hand (23 / 4 / 4); no reclassification.
- Web: the API returns `[{name, domain}]`; tag colour per domain (one CSS
  class per domain, neutral for unknown); `skip`/`unknown` stay core tags;
  the hard-coded list in `web/src/ui/Tag.tsx` and per-type classes go.
- ~38 test files renamed in the same commit.

## Acceptance

- [ ] `grep -rn "devops\.\|docs\.doc_question\|access_request"` over code,
      tests, web, evals: only old migrations and history.
- [ ] Live db wiped with a backup on disk; `alembic current` = head.
- [ ] `run_triage_eval` run and reported (labels changed in the prompt).
- [ ] Web renders tags from the API list; `npm run build` passes.
- [ ] `test_dependency_rule.py` G1 still holds with `plugins/ops`.
- [ ] Whole suite green; `code-review` done.
