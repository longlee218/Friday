Type: prototype
Status: claimed
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
