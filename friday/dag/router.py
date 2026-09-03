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

from friday.dag.api_issue import graph as api_issue
from friday.dag.api_issue.graph import build_api_issue_dag
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


def build_simple_dag(task_type: str, params_cls: type[Params]) -> DAG:
    """`prepare`, then ask for what is missing or hand the rest over — what
    every type without an investigation needs (D1). There is nothing here
    worth a second node yet; build one when there are steps worth skipping,
    not before.
    """
    return DAG(
        name=task_type,
        nodes=(
            prepare_node(
                task_type,
                params_cls,
                on_ready=lambda filled: plan_by_required_parameters(task_type, filled),
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

    Every entry in `PARAMS` gets a graph: `api_issue` its own, everything else
    the one-node graph `build_simple_dag` builds. `dag_for` never answers "no
    graph" for a type this covers, which is every classifiable type there is.
    """
    EDGE_ROUTER.clear()
    register_dag("api_issue", build_api_issue_dag())

    for task_type, params_cls in PARAMS.items():
        if task_type == "api_issue":
            continue
        register_dag(task_type, build_simple_dag(task_type, params_cls))

    # Each graph builds its own agents: which node gets which configuration
    # block, tools and server is the graph's own business, and this module
    # knowing the answer is what ticket 15 took out of it.
    agents = api_issue.build_agents(config, skills, servers)
    if agents:
        log.info("api_issue graph: agents for %s", ", ".join(sorted(agents)))
        absent = api_issue.absent_servers(agents, servers)
        if absent:
            log.info(
                "api_issue graph: no %s server — the nodes needing it will skip",
                ", ".join(absent),
            )
    else:
        log.info(
            "api_issue graph: no node agents configured — it will hand over "
            "every report rather than investigate one"
        )

    # Stored for the runner to hand down through `DAGDeps`. Kept here rather
    # than closed over inside the graph so the graph stays testable without
    # either a model or a tool server.
    DAG_DEPS_EXTRA["api_issue"] = {**agents, "context_store": context_store}
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
