"""Which graph runs a task type, and the clocks it must respect.

`EDGE_ROUTER` maps a task type to its `DAG`; `register_dags` fills it at boot
from the registry (`friday.dag.task_types`), which each task type registers
itself into — so this module names no task type. Ticket 11 moved the
per-type building (`api_issue`'s investigation graph, the one-node graph for the
rest) out to `task_types.py`; what stays here is the map, the clock checks, and
the one-node graph builder every simple type shares.

`dag_for` returning `None` for a *known* type would be a wiring bug, not a normal
outcome; ticket 04 deleted the second way of deciding what to do with a task, and
with it the branch that used to read that `None`.
"""

from __future__ import annotations

import logging
from typing import Any

from friday.config import ConfigError
from friday.sdk.workflow import DAG
from friday.dag import registry
from friday.dag.prepare import plan_by_required_parameters, prepare_node
from friday.domain.models import Params

__all__ = [
    "EDGE_ROUTER",
    "NODE_CLOCK_MARGIN_SECONDS",
    "build_simple_dag",
    "check_graphs",
    "check_node_clocks",
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


# --- two clocks ---------------------------------------------------------------

#: How much longer a model node's clock must run than its harness's. The
#: harness bounds a run with `timeout_seconds` and turns its own expiry into a
#: `last_error` the node can read; the node's clock expiring first cancels the
#: run instead, and a cancellation is a `BaseException` nothing in the harness
#: sees (board `read-it-the-way-the-operator-does`, finding E). The margin is
#: what lets the inner clock fire first; equal clocks are a race.
NODE_CLOCK_MARGIN_SECONDS = 5.0


def check_node_clocks(dags: Any, agents: dict[str, Any]) -> None:
    """Refuse a model node whose timeout does not leave its harness room.

    Run by `check_graphs` straight after the configuration is loaded, and
    again by `register_dags` on what it registers — so a misconfigured
    timeout stops the process at boot rather than surfacing as a cancelled
    run on a task nobody is watching. A model node
    with no timeout of its own has one clock and nothing to check.
    """
    for dag in dags:
        for node in dag.nodes:
            if node.agent is None or node.timeout_seconds is None:
                continue
            agent = agents.get(node.agent)
            if agent is None:
                raise ConfigError(
                    f"graph {dag.name!r}: node {node.name!r} runs agent "
                    f"{node.agent!r}, which is not configured under agents:"
                )
            floor = agent.timeout_seconds + NODE_CLOCK_MARGIN_SECONDS
            if node.timeout_seconds < floor:
                raise ConfigError(
                    f"graph {dag.name!r}: node {node.name!r} times out after "
                    f"{node.timeout_seconds}s, but agent {node.agent!r} is given "
                    f"{agent.timeout_seconds}s — the node's clock must be at "
                    f"least {floor}s, or it cancels the run before the harness "
                    "can say why it stopped"
                )


def check_graph_clocks(dags: Any, budgets: dict[str, float]) -> None:
    """Refuse a graph whose nodes can outlast the budget for one task.

    **What this is really guarding is the pool, not the graph.** Ticket 13
    stopped one long task from holding the batch by working it side by side,
    and rested on `api_issue` being "bounded at five minutes". That was true
    of the one-node graph it was written against. Measured 2026-09-22, the
    seven-node graph summed to 340s and three of its nodes had no ceiling at
    all — so the sentence had stopped being true and nothing anywhere said
    so. With `workflows.concurrency` at 2, two of those are both slots.

    Two rules, and the first is the one that matters: **every node has a
    ceiling.** A node without one is not bounded by a large number, it is
    unbounded, and a sum that skips it is a sum that means nothing.

    At boot, like `check_node_clocks` and for the same reason: a clock that
    does not add up is a configuration mistake, and cancelling a run half
    way leaves a task that has read a log, told the reporter it was working
    and written nothing.
    """
    for dag in dags:
        budget = budgets.get(dag.name)
        if budget is None:
            continue
        loose = [n.name for n in dag.nodes if n.timeout_seconds is None]
        if loose:
            raise ConfigError(
                f"graph {dag.name!r}: node(s) {loose} have no timeout, so the "
                f"graph has no bound however long {dag.name}.timeout_seconds "
                f"is — one hung read holds a pool slot for the life of the "
                f"process"
            )
        total = sum(n.timeout_seconds or 0.0 for n in dag.nodes)
        if total > budget:
            raise ConfigError(
                f"graph {dag.name!r}: its nodes can take {total}s together, "
                f"over the {budget}s budget in {dag.name}.timeout_seconds. "
                f"Raise the budget, or lower a node's clock — "
                + ", ".join(f"{n.name} {n.timeout_seconds}s" for n in dag.nodes)
            )


# --- the one-node graph, for a type with no investigation --------------------


def build_simple_dag(
    task_type: str,
    params_cls: type[Params],
    *,
    budget_tokens: int | None = None,
    extractor: Any = None,
) -> DAG:
    """`prepare`, then ask for what is missing or hand the rest over — what
    every type without an investigation needs (D1). There is nothing here
    worth a second node yet; build one when there are steps worth skipping,
    not before.

    `budget_tokens` — board `what-the-room-already-knows`, ticket 08 —
    passes straight through to `prepare_node`, the same for every type: node
    0's budget is one value shared by every task type, not one per type.
    (A channel context store travelled beside it until the YAML files went,
    board `read-it-the-way-the-operator-does`, ticket 10: the room is rows
    now, read through `deps.db` like everything else node 0 reads.)

    `extractor` is the configured `extractor` agent, if there is one. Node 0
    calls it, which makes node 0 a model node: it names the agent and gets a
    clock that is the extractor's own plus `NODE_CLOCK_MARGIN_SECONDS` —
    derived rather than declared, so raising the extractor's timeout in
    config.yaml moves node 0's with it instead of slipping under it.
    """
    return DAG(
        name=task_type,
        nodes=(
            prepare_node(
                task_type,
                params_cls,
                on_ready=lambda filled: plan_by_required_parameters(task_type, filled),
                budget_tokens=budget_tokens,
                agent=None if extractor is None else "extractor",
                timeout_seconds=(
                    None
                    if extractor is None
                    else extractor.timeout_seconds + NODE_CLOCK_MARGIN_SECONDS
                ),
            ),
        ),
    )


def check_graphs(config: Any) -> None:
    """Refuse a configuration under which a model node's clock would cut its
    harness short — from the configuration alone, so the composition root runs
    it straight after `load_config`, before it opens the database or builds
    anything a graph is later handed. `register_dags` checks again what it
    actually registers.

    **Fills the registry as it goes** — the graphs are what the clocks are
    checked on — and the composition root leans on that: it runs `check_graphs`
    first, then `register_extractors`, which reads `registry.decision_params()`
    to know which types to wire. So this is not a throwaway side effect;
    `register_extractors` depends on it, and `register_dags` clears and refills
    the registry afterwards with the run's real servers. Reorder these three and
    `register_extractors` would wire nothing — the order is the contract."""
    from friday.dag.task_types import BootContext, register_all

    register_all(BootContext(config))
    dags = [registry.dag_of(name) for name in registry.specs()]
    check_node_clocks(dags, config.agents)
    check_graph_clocks(dags, registry.BUDGETS)


def register_dags(
    config: Any,
    *,
    servers: dict[str, Any] | None = None,
    skills: Any = None,
    #: Where a node's model call is recorded, and what it has already spent —
    #: the same two the extractors and the responder are built with. `None`
    #: for both is a build with no `diagnose` agent, which is every test that
    #: does not set one up.
    record: Any = None,
    spent: Any = None,
    #: The store the workflow adapter rebuilds each run's `Deps` from, and
    #: writes `node_runs` through. `None` registers the graphs for the pool to
    #: read (`dag_for`) but not on the DBOS adapter — every test that drives a
    #: node directly with a stub rather than running the durable workflow.
    db: Any = None,
) -> None:
    """Register every graph this build knows about, from the registry.

    Each task type registers itself (`friday.dag.task_types.register_all`); this
    reads the registry and maps each type to its graph. Nothing here names a
    type — adding one is registering a `TaskTypeSpec`, not editing this function.

    Idempotent: `register_all` clears the registry and `EDGE_ROUTER` starts from
    empty, so the "refuses to overwrite" guard stays live for everyone else
    rather than being disarmed at each call site.

    `dag_for` never answers "no graph" for a type this covers, which is every
    classifiable type there is.
    """
    from friday.dag.task_types import BootContext, register_all

    register_all(
        BootContext(
            config=config,
            record=record,
            spent=spent,
            servers=dict(servers or {}),
            skills=skills,
        )
    )

    EDGE_ROUTER.clear()
    for task_type in registry.specs():
        register_dag(task_type, registry.dag_of(task_type))

    check_node_clocks(EDGE_ROUTER.values(), config.agents)
    check_graph_clocks(EDGE_ROUTER.values(), registry.BUDGETS)

    # Register the same graphs on the DBOS adapter (ticket 06), the way the pool
    # runs them past node 0. The adapter rebuilds each run's `Deps` from a
    # serializable scope key inside the workflow — a live source cannot cross a
    # step boundary — so it reads the task back from the store and the run
    # handles from the registry's `DEPS_EXTRA`/`SERVERS`, filled by
    # `register_all` above.
    if db is not None:
        _register_on_adapter(db)


def _register_on_adapter(db: Any) -> None:
    from dataclasses import asdict

    from friday.sdk.workflow import Deps, NodeRun
    from friday.workflow import adapter

    async def deps_factory(scope_key: dict[str, Any]) -> Deps:
        task_id = scope_key.get("task_id")
        task = await db.task(task_id) if task_id is not None else None
        return Deps(
            task=task,
            db=db,
            servers=dict(registry.SERVERS),
            extra=dict(registry.DEPS_EXTRA.get(scope_key.get("task_type", ""), {})),
        )

    def recorder_factory(scope_key: dict[str, Any]):
        task_id = scope_key.get("task_id")

        async def record(run: NodeRun) -> None:
            await db.record_node_run(task_id=task_id, **asdict(run))

        return record

    adapter.clear_graphs()
    for dag in EDGE_ROUTER.values():
        adapter.register_graph(dag, deps_factory, recorder_factory=recorder_factory)
