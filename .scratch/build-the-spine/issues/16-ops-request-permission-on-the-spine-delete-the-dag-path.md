Status: ready-for-agent
Blocked by: 14, 15

# `ops.request_permission` on the spine; delete the DAG path

Decisions: [What replaces `params`](../../domains-plug-in/issues/05-what-replaces-params.md),
[The target module layout](../../domains-plug-in/issues/09-the-target-module-layout.md) §2, §4, §7, §10,
[The action contract](../../domains-plug-in/issues/01-is-the-action-spec-the-task-contract.md) (deleted fields),
map § Decisions ("The extractor is deleted outright").

## Goal

- `plugins/ops/actions/request_permission/` on the spine (no enricher).
- Deleted: `kernel/dag/`, `kernel/extraction/`, `text/param_hygiene.py`,
  every `Params` class, `sdk/validation.py`, `sdk/workflow.py`,
  `sdk/workflow_state.py`, `sdk/model.py`, `TaskTypeSpec`,
  `PluginAPI.task_type`, `caps`, `DAGState`/`DagState`/`NodeRun`,
  `AccessRequestParams`, `askable_fields`, `ExtractionMark`,
  `mark_extraction`, `set_task_params`, `workflows.max_asks`,
  `auto_ask_for_details`, `use_responder`, `extraction_budget_tokens`.
  `sdk/outbox.py` (`Kind`) → `kernel/outbox.py`.
- Alembic schema-only: drop `tasks.params` (and `dag_state`/`node_runs`).
- ADR: deleting the extractor (hard to reverse) — `docs/adr/0001-*.md`.

## Acceptance

- [ ] `grep` for every deleted name: none outside history/migrations.
- [ ] `friday/sdk/` holds only `plugin.py`, `action.py`, `agent.py`,
      `toolset.py`, `sources.py`, `memory.py`, `redact.py`, `prompt.py`,
      `testing/` (guard test).
- [ ] `dbos` is still imported by exactly one module (the adapter, now under
      `kernel/spine/`), outbox sends included (guard).
- [ ] `run_triage_eval` reported if the prompt bytes changed.
- [ ] `docs/DESIGN.md` § What exists has no DAG left; ADR written.
- [ ] Whole suite green; `code-review` done.
