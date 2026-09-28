Status: done
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

- [x] `grep -rn "devops\.\|docs\.doc_question\|access_request"` over code,
      tests, web, evals: only old migrations and history.
- [x] Live db wiped with a backup on disk; `alembic current` = head.
- [ ] `run_triage_eval` run and reported (labels changed in the prompt).
      **Deferred by the operator (2026-09-28):** no OpenRouter account yet;
      run it once `OPENROUTER_API_KEY` is in `.env`.
- [x] Web renders tags from the API list; `npm run build` passes.
- [x] `test_dependency_rule.py` G1 still holds with `plugins/ops`.
- [x] Whole suite green; `code-review` done.

## Built (2026-09-28)

- Branch `feat/build-the-spine-02`. Suite 1579 passed, 1 skipped (baseline
  1578 + the `/api/actions` contract test); `code-review` (standards + spec)
  done, its findings fixed.
- Live db backed up to `data/friday.db.bak-pre-build-the-spine-02`, deleted,
  `alembic upgrade head` → `eb2f1050f114 (head)`. `friday.system.db` kept (no
  pending workflows).
- Operator memory: `import_context_files.py` reads a `context/` YAML directory
  that no longer exists and writes only `fact`/`person`, so the 9 active admin
  rows were exported and written back through `memory.write.add` with
  `devops.*` → `backend.*` (order: environment, project, service, route,
  fact). Diff against the backup: identical.
- Remaining grep hits are history (`extractor_access_request` in two
  docstrings, old migrations) and `devops.json`, the external MCP server's
  token file in `test_mcp_auth.py`.
- **Deferred (operator, 2026-09-28):** `run_triage_eval` not run — no
  OpenRouter account yet.
  `config.yaml` references `OPENROUTER_API_KEY`, which `.env` does not set.
  The prompt now names the full labels (`ops.request_permission`, …).
