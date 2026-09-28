Type: prototype
Status: resolved
Blocked by: 03, 10

# The target module layout

## Question

Stub the target tree for `friday/` and `plugins/` under the spine and the decided
standard: where the spine lives (Intake, Planner, GatePlan, WorkflowRunner),
core step types and toolsets, what `friday/kernel` looks like once the
extractor, `Params`, per-type DAGs and the hand-written router are gone, which
tiny directories fold (`inbox`, `pool`, `outbox`), how the grab-bag files split
(`domain/models.py` 980 lines, `harness/harness.py` 905,
`repositories/memory.py` 777, `repositories/tasks.py` 698), and the `DAGState`
(runtime) vs `DagState` (DB row) collision. Show `plugins/backend/` and
`plugins/ops/`.

> Note from "The plugin API surface" (03, amended 2026-09-28): two new core
> toolsets need a home — `core.shell` (read-command allowlist, declared SSH
> hosts, refusals to `audit_log`) and `core.workspace` (`/tmp/friday/<task_id>/`
> via pydantic-ai-harness `FileSystem`). `plugins/backend/config.py` goes
> (`Plugin.config` deleted); `container_roots` / `not_ours` become constants.

## Answer

Decided 2026-09-28 (prototype + grilling, one question at a time). Prototype:
branch `prototype/target-module-layout`, file
`.scratch/domains-plug-in/target_module_layout_STUB.md` — the full target tree.

1. **The spine is one package**, `friday/kernel/spine/`, one file per stage:
   `workflow.py` (the DBOS pass), `intake.py`, `plan.py` (`Plan`, steps,
   `step_key`, hash, plus `Ask`/`Reply`/`HandOver` beside `Outcome`),
   `planner.py`, `plan_gate.py`, `runner.py`, `deliver.py`. `triage/` and
   `responder/` stay outside it.
2. **One-file packages fold into modules**: `kernel/inbox.py`,
   `kernel/outbox.py` + `kernel/outbox_card.py`, `kernel/pool.py` (shrinks: the
   run loop moves to the spine), `kernel/text_transform.py`. `dag/`,
   `extraction/`, `text/param_hygiene.py` are deleted; `tools/` becomes
   `toolsets/` (`core.memory`, `core.skills`, `core.shell`, `core.workspace`).
3. **Inside a plugin**: root = `__init__.py` (`PLUGIN`), `placement.py`
   (enricher), `memory.py`; plus `actions/<action_name>/` (`__init__.py` with
   the `Action`, `recognition.py`), `agents/` (`diagnose.py`, `explain.py`),
   `toolsets/`. `plugins/ops/` has the same shape with no enricher.
   `plugins/devops/` and `plugins/docs/` go.
   - **`sources/` folds into `toolsets/`** (operator's call: one place to
     control). One file per data source holds both the tools and the client
     that reaches out (`logs.py`, `code.py`, `docs.py`, `db.py`,
     `release.py`). `friday/sdk/sources.py` keeps `Reads` + ports. The guard
     `tests/test_sources_are_the_only_door.py` gets `toolsets/` as its
     allow-list; replay is unaffected (it swaps `Reads` via `CannedReads`).
     **Amends DESIGN D6 / "Three layers"**: noted in `docs/DESIGN.md` as
     decided, not built.
4. **`kernel/domain/models.py`** → `messages.py` (`MentionType`,
   `InboundEvent`, `Artifact`), `tasks.py` (`Task`, `RunningTask`),
   `outbound.py` (`Outbound`, `payload_hash*`, `AuditEntry`), `memory.py`
   (every memory type and refusal), `monitor.py` (`ToolCall`, `ModelCall`,
   `MessageFlow`, `MonitorEvent`, `MonitorSnapshot`), `state.py`
   (`FridayState`). `AccessRequestParams`, `askable_fields`, `ExtractionMark`
   deleted.
5. **`kernel/harness/harness.py`** → `harness.py` (the loop), `retry.py`,
   `model_client.py`. `@tool` and `Refused` move to `friday/sdk/toolset.py`;
   the harness imports them from there. No re-export.
6. **`store/repositories/memory.py`** → `memory.py` (reads + writes) +
   `memory_candidates.py`.
7. **`store/repositories/tasks.py`** → `tasks.py` (lifecycle, `pass_no`),
   `task_conversation.py`, `compaction.py`; new `plans.py` (plans + step
   results; the pending `Ask` replaces `set_pause` / `pauses_for`).
   `extraction_mark`, `mark_extraction`, `set_task_params` deleted.
8. **`DAGState` + `DagState` become one class**: `StepResult`, the ORM row
   (`step_results`, key `(task_id, step_key)`) the runner reads and writes
   directly — no runtime twin, no converter. `DAGState`, `DagState`,
   `NodeRun`, `MissingNodeResult` deleted; no `step_runs` table (attempts
   are in `model_calls` / `tool_calls`). `plans` stays (ticket 14).
   Elsewhere the naming rule is unchanged: a domain dataclass and its ORM
   row may share a name, told apart by module.
9. **Other files over 200 lines** (`ops/api.py` 1108,
   `harness/instruction_prompt.py` 868, `config.py`, `store/schema.py`,
   `store/_common.py`) split when the build touches them; not drawn here.
10. **`friday/sdk/` holds only what a plugin imports**: `plugin.py`,
    `action.py`, `agent.py`, `toolset.py`, `sources.py`, `memory.py`,
    `redact.py`, `prompt.py`, `testing/`. `model.py` deleted (plugins no
    longer call a model); `outbox.py` (`Kind`) → `kernel/outbox.py`;
    `actions.py` → `kernel/spine/plan.py`; `validation.py`, `workflow.py`,
    `workflow_state.py` deleted. A plugin's own result types (`Diagnosis`,
    `Explanation`) live in its `agents/`.

Build consequences: `development-rules.md` gets the 200-line soft target and
the `sdk/` rule; the guard's allow-list moves to `plugins/*/toolsets/` and
`core.shell`; DESIGN.md § What exists / Layout rewritten in the build commit
that moves the files.
