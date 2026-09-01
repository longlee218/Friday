"""Which DAG runs a task type.

One dict. Registering a workflow is adding an entry; nothing else in the
system needs to change, which is the property ticket 32 exists to buy.

A task type with no DAG is not an error — the deterministic path in
`friday.workflows` still handles it. The router answers "is there a graph for
this?", and `None` means "no, use the simple path".
"""

from __future__ import annotations

from friday.dag import DAG

__all__ = ["EDGE_ROUTER", "dag_for", "register_dag"]

#: Task type -> the DAG that runs it. Populated by each workflow module at
#: import; read by `WorkflowRunner._plan`.
EDGE_ROUTER: dict[str, DAG] = {}


def register_dag(task_type: str, dag: DAG) -> DAG:
    """Register `dag` as the workflow for `task_type`.

    Refuses to overwrite. Two DAGs claiming one task type is a wiring
    mistake, and the second registration silently winning is the kind that
    surfaces as "why is it running the old graph?" a week later.
    """
    if task_type in EDGE_ROUTER:
        raise ValueError(
            f"a DAG is already registered for task type {task_type!r}: "
            f"{EDGE_ROUTER[task_type].name!r}"
        )
    EDGE_ROUTER[task_type] = dag
    return dag


def dag_for(task_type: str) -> DAG | None:
    """The DAG for this task type, or None to use the deterministic path."""
    return EDGE_ROUTER.get(task_type)
