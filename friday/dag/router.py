"""Which graph runs a task type, and what it is built from.

One module answers both questions. Registering a graph is adding an entry to
`EDGE_ROUTER`; nothing else in the system needs to change, which is the
property ticket 32 exists to buy. Building the agents behind a graph's nodes
is wiring, and it used to live in a second module that this one's `register_
dag` and `dag_for` were never read apart from — the composition root calls
`register_dags` once, which is the only caller of everything else here.

Every classifiable task type has a graph — `register_dags` covers every entry
in `PARAMS`, `api_issue` by name and everything else as a one-node graph built
here. `dag_for` returning `None` for a *known* type would be a wiring bug, not
a normal outcome; ticket 04 deleted the second way of deciding what to do with
a task, and with it the branch that used to read that `None`.
"""

from __future__ import annotations

import logging
from typing import Any

from friday.dag.engine import DAG
from friday.dag.prepare import plan_by_required_parameters, prepare_node
from friday.domain.models import PARAMS, Params

__all__ = [
    "EDGE_ROUTER",
    "build_simple_dag",
    "dag_for",
    "register_dag",
    "register_dags",
]

log = logging.getLogger(__name__)

#: Task type -> the DAG that runs it. Populated by `register_dags` at
#: startup; read by `Pool._plan`.
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
    """The DAG for this task type. `None` for a type `register_dags` has
    covered is a wiring bug — `Pool._plan` asserts on it rather
    than falling back to a second way of deciding what to do."""
    return EDGE_ROUTER.get(task_type)


# --- the one-node graph, for a type with no investigation --------------------


def build_simple_dag(
    task_type: str,
    params_cls: type[Params],
    *,
    budget_tokens: int | None = None,
    context_store: Any = None,
) -> DAG:
    """`prepare`, then ask for what is missing or hand the rest over — what
    every type without an investigation needs (D1). There is nothing here
    worth a second node yet; build one when there are steps worth skipping,
    not before.

    `budget_tokens` — board `what-the-room-already-knows`, ticket 08 —
    and `context_store` — ticket 15 — both pass straight through to
    `prepare_node`, the same for every type: node 0's budget and the
    channel context store are each one value shared by every task type,
    not one per type.
    """
    return DAG(
        name=task_type,
        nodes=(
            prepare_node(
                task_type,
                params_cls,
                on_ready=lambda filled: plan_by_required_parameters(task_type, filled),
                budget_tokens=budget_tokens,
                context_store=context_store,
            ),
        ),
    )


def register_dags(
    config: Any,
    *,
    servers: dict[str, Any] | None = None,
    skills: Any = None,
    context_store: Any = None,
) -> None:
    """Register every graph this build knows about.

    Idempotent, and it says so once here rather than by disarming
    `register_dag`'s guard at each call. It used to `pop` the key immediately
    before every registration, which meant the "refuses to overwrite" check
    could not fire anywhere but in a test — a guard deleted at every call site
    that mattered (ticket 13). Starting from empty states the same intention
    and leaves the guard live for everyone else.

    Every entry in `PARAMS` gets the same one-node graph. There was a
    five-node `api_issue` investigation — read the logs, find the code,
    analyse, propose a patch, compose a reply — and the operator removed it:
    it was a workflow nobody had described, built from a guess at what
    investigating an API fault looks like, and every node of it skipped on
    every run because no tool server was ever configured.

    `dag_for` never answers "no graph" for a type this covers, which is every
    classifiable type there is. Build a multi-node graph when there are steps
    worth skipping and somebody has said what they are.

    **`context_store` reaches node 0 by closure now, not through
    `DAG_DEPS_EXTRA`** (board `what-the-room-already-knows`, ticket 15).
    This parameter used to be written into `DAG_DEPS_EXTRA["context_store"]`
    — a dict keyed by *task type*, per its own annotation — so the value
    landed under a key no `DAGDeps.extra` lookup, keyed by `task.type`,
    could ever reach: `deps.extra.get(task.type, {})` never once produced
    `"context_store"`. Found while wiring `build_full_context`'s own need
    for it, not by anything that had been reading it — nothing was. Passed
    straight to `build_simple_dag` instead, the same way `budget_tokens`
    already is.
    """
    EDGE_ROUTER.clear()
    budget_tokens = config.context.extraction_budget_tokens
    for task_type, params_cls in PARAMS.items():
        register_dag(
            task_type,
            build_simple_dag(
                task_type,
                params_cls,
                budget_tokens=budget_tokens,
                context_store=context_store,
            ),
        )

    # No graph has a node agent any more — the one that did was `api_issue`'s
    # investigation, and it is gone. `DAG_DEPS_EXTRA` stays empty and the
    # dict stays, because it is what a graph's agents would be handed down
    # through, and `Pool` reads it either way.
    DAG_DEPS_EXTRA.clear()
    # Replaced, not merged. Merging means a second call — a test, a restart in
    # the same process — leaves the previous run's servers reachable, and a
    # closed connection that is still in the dict is worse than an absent one:
    # the node stops skipping and starts failing.
    DAG_SERVERS.clear()
    DAG_SERVERS.update(servers or {})


#: Node name -> agent, per task type. Read by `Pool` when it builds
#: `DAGDeps`; empty until `register_dags` runs.
DAG_DEPS_EXTRA: dict[str, dict[str, Any]] = {}

#: Tool servers available to every graph, by name.
DAG_SERVERS: dict[str, Any] = {}
