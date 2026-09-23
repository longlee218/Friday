"""The one place a task type is known — filled by `register()`, read by the rest.

Ticket 11 folds three hand-maintained maps into one registry: `PARAMS` (the
task-type -> `Params` catalog), the extractor registry, and the router's
`_graphs` builder. A task type now *registers itself* — a `TaskTypeSpec` handed
to `register_task_type` at boot — and the router, the extraction runner and
triage read the registry instead of naming any type.

The registry lives here, above `friday.domain` (so it can hold graph builders,
which are `sdk`/`dag` types) and below the composition root (which fills it). The
value layer never reaches up into it: `friday.domain.actions.make_decided` takes
the `params` mapping as an argument, and the store takes the decision names as a
query argument — so nothing under this module has to import it.

`DEPS_EXTRA`/`SERVERS` are the per-run handles the DBOS adapter rebuilds a run's
`Deps` from — a live source cannot cross a step boundary, so it is looked up by
task type here. They are ticket-13-transitional: the typed per-run `Deps` factory
replaces them next.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from friday.kernel.registry import Registry
from friday.sdk.workflow import DAG
from friday.sdk.plugin import TaskTypeSpec

__all__ = [
    "BUDGETS",
    "DEPS_EXTRA",
    "SERVERS",
    "TASK_TYPES",
    "clear",
    "dag_of",
    "decision_params",
    "decisions",
    "register_task_type",
    "specs",
]

#: The registry itself — a kernel `Registry` (the `PluginAPI` shape), so a task
#: type's registration is `api.task_type(spec)` and nothing here names a type.
TASK_TYPES = Registry()

#: Per-task-type run handles the adapter rebuilds `Deps` from (sources, the
#: sender/approver identities). Keyed by task type; populated by the type's own
#: `register()`, so the router names no type.
DEPS_EXTRA: dict[str, dict[str, Any]] = {}

#: Tool servers available to every graph, by name.
SERVERS: dict[str, Any] = {}

#: How long one task of a type may hold a pool slot, when it declares a bound.
#: Populated by the type's own registration; read by `check_graph_clocks`, so the
#: router no longer names the one type that has a budget.
BUDGETS: dict[str, float] = {}


def register_task_type(
    spec: TaskTypeSpec,
    *,
    deps_extra: Mapping[str, Any] | None = None,
    budget: float | None = None,
) -> None:
    """Add one task type: its spec, the run handles its graph needs, and the
    per-task budget it declares. A duplicate name refuses (the `Registry`
    guard)."""
    TASK_TYPES.task_type(spec)
    if deps_extra:
        DEPS_EXTRA[spec.name] = dict(deps_extra)
    if budget is not None:
        BUDGETS[spec.name] = budget


def clear() -> None:
    """Forget every registered task type — for the composition root and for
    tests that register their own set."""
    global TASK_TYPES
    TASK_TYPES = Registry()
    DEPS_EXTRA.clear()
    SERVERS.clear()
    BUDGETS.clear()


def specs() -> Mapping[str, TaskTypeSpec]:
    return TASK_TYPES.task_types()


def decision_params() -> dict[str, type]:
    """The `task_type -> Params` mapping the triage schema and the store's
    classifiable set are built from — the registry in the shape the old `PARAMS`
    had, so callers that took `PARAMS` take this."""
    return {name: spec.params for name, spec in TASK_TYPES.task_types().items()}


def decisions() -> tuple[str, ...]:
    """The closed set triage may conclude — every registered task type plus
    `skip` — the old `DECISIONS` read from the registry."""
    from friday.domain.models import SKIP

    return (*TASK_TYPES.task_types(), SKIP)


def dag_of(task_type: str) -> DAG:
    """The built graph for a task type. Its `graph` factory takes a `Deps`;
    ticket 11 builds the graph at boot and ignores it, ticket 13 will not."""
    spec = TASK_TYPES.task_types()[task_type]
    assert spec.graph is not None, f"task type {task_type!r} registered no graph"
    return spec.graph(None)


