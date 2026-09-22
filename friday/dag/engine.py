"""Compatibility shim over the workflow port.

The hand-written DAG engine — `DAGRunner` and its checkpoint/resume — was
retired onto DBOS in ticket 06: durability, step memoization and resume are the
adapter's now (`friday/workflow/adapter.py`), and the graph vocabulary lives in
the port (`friday/sdk/workflow.py`). This module re-exports that vocabulary
under the old names so the graph nodes and their tests keep importing from one
place. `DAGRunner` is gone; there is nothing to run here any more.

`DAGDeps` is the old name for the port's `Deps`.
"""

from friday.sdk.workflow import (
    DAG,
    STATUSES,
    DAGState,
    Deps as DAGDeps,
    Edge,
    Node,
    NodeRun,
    envelope,
    status_of,
)

__all__ = [
    "DAG",
    "DAGDeps",
    "DAGState",
    "Edge",
    "Node",
    "NodeRun",
    "STATUSES",
    "envelope",
    "status_of",
]
