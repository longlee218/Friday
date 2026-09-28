"""The one place a task type is known — filled by `register()`, read by the rest.

Ticket 11 folds three hand-maintained maps into one registry: `PARAMS` (the
task-type -> `Params` catalog), the extractor registry, and the router's
`_graphs` builder. A task type now *registers itself* — a `TaskTypeSpec` handed
to `register_task_type` at boot — and the router, the extraction runner and
triage read the registry instead of naming any type.

The registry lives here, above `friday.kernel.domain` (so it can hold graph builders,
which are `sdk`/`dag` types) and below the composition root (which fills it). The
value layer never reaches up into it: `friday.kernel.domain.triage.make_decided` takes
the `params` mapping as an argument, and the store takes the decision names as a
query argument — so nothing under this module has to import it.

`SERVERS` holds the tool servers a run opened, by name; the DBOS adapter reads
them (a live source cannot cross a step boundary) into the base `Deps` it rebuilds
inside the run. A task type's own extra handles are no longer a global dict — the
type's `deps` factory (ticket 13) builds them per run, read here through `deps_of`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from friday.kernel.registry import Registry
from friday.sdk.workflow import DAG, Deps
from friday.sdk.plugin import TaskTypeSpec

__all__ = [
    "DAGS",
    "SERVERS",
    "TASK_TYPES",
    "clear",
    "dag_of",
    "decision_params",
    "decisions",
    "deps_of",
    "register_task_type",
    "specs",
]

#: The registry itself — a kernel `Registry` (the `PluginAPI` shape), so a task
#: type's registration is `api.task_type(spec)` and nothing here names a type.
TASK_TYPES = Registry()

#: The built `DAG` for each registered type. Built once at registration (node 0
#: plus the type's investigation, if any) and stored here, rather than rebuilt on
#: every `dag_of` — a plugin's graph builds a model harness, and building it
#: twice a boot is waste the store avoids.
DAGS: dict[str, DAG] = {}

#: Tool servers available to every graph, by name.
SERVERS: dict[str, Any] = {}


def register_task_type(spec: TaskTypeSpec, *, dag: DAG) -> None:
    """Add one task type: its spec and its built `DAG`. A duplicate name
    refuses (the `Registry` guard). A type's live handles are on the spec
    (`spec.deps`), built per run, not here."""
    TASK_TYPES.task_type(spec)
    DAGS[spec.name] = dag


def clear() -> None:
    """Forget every registered task type — for the composition root and for
    tests that register their own set."""
    global TASK_TYPES
    TASK_TYPES = Registry()
    DAGS.clear()
    SERVERS.clear()


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
    from friday.kernel.domain.tasks import SKIP

    return (*TASK_TYPES.task_types(), SKIP)


def dag_of(task_type: str) -> DAG:
    """The built graph for a task type — stored at registration by
    `register_task_type`. The run's typed `Deps` are built by `deps_of`."""
    return DAGS[task_type]


def deps_of(task_type: str) -> Callable[[Deps], Deps] | None:
    """The task type's per-run deps factory, or `None` when its base `Deps` is
    enough. The adapter applies it to the base `Deps` it builds per run."""
    spec = TASK_TYPES.task_types().get(task_type)
    return spec.deps if spec is not None else None


