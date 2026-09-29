"""Which graph runs a task type, and the clocks it must respect.

`EDGE_ROUTER` maps a task type to its `DAG`; `register_dags` fills it at boot
from the registry (`friday.kernel.dag.task_types`), which each task type registers
itself into — so this module names no task type. Ticket 11 moved the
per-type building (`trace_problem`'s investigation graph, the one-node graph for the
rest) out to `task_types.py`; what stays here is the map, the clock checks, and
the one-node graph builder every simple type shares.

`dag_for` returning `None` for a *known* type would be a wiring bug, not a normal
outcome; ticket 04 deleted the second way of deciding what to do with a task, and
with it the branch that used to read that `None`.
"""

from __future__ import annotations

import logging
from typing import Any

from friday.kernel.config import ConfigError
from friday.kernel.dag import registry
from friday.kernel.dag.prepare import plan_by_required_parameters, prepare_node
from friday.kernel.domain.tasks import Params
from friday.sdk.workflow import DAG

__all__ = [
    "EDGE_ROUTER",
    "build_simple_dag",
    "check_deps",
    "check_graphs",
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

    `extractor` is the resolved `EXTRACTOR` agent (`None` only from a test
    stand-in). Node 0
    calls it, which makes node 0 a model node: it names the agent.
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
            ),
        ),
    )


def check_graphs(config: Any) -> None:
    """Refuse a configuration a graph cannot be built from — an undeclared
    tier, a `deps` factory that cannot build — from the configuration alone,
    so the composition root runs it straight after `load_config`, before it
    opens the database or builds anything a graph is later handed.

    **Fills the registry as it goes** — the graphs are what is checked — and
    the composition root leans on that: it runs `check_graphs`
    first, then `register_extractors`, which reads `registry.decision_params()`
    to know which types to wire. So this is not a throwaway side effect;
    `register_extractors` depends on it, and `register_dags` clears and refills
    the registry afterwards with the run's real servers. Reorder these three and
    `register_extractors` would wire nothing — the order is the contract."""
    from friday.kernel.dag.task_types import BootContext, register_all

    register_all(BootContext(config))
    check_deps()


def check_deps() -> None:
    """Refuse a task type whose `deps` factory cannot build its run `Deps`.

    Runs every registered `deps` factory against a base `Deps` at boot and
    confirms the run `Deps` it returns can be *constructed* — every field
    satisfied (§5.2). A factory that leaves a required field unset (`sender`,
    say — a row queued mid-run would then have no identity to send as) fails
    here rather than mid-investigation on a task nobody is watching. This is a structural check: it
    catches a *missing* field, not a wrong or empty value — no realistic factory
    hardcodes one. A type whose base `Deps` is enough (`spec.deps is None`) has
    nothing to check.
    """
    from friday.sdk.workflow import Deps

    for name in registry.specs():
        enrich = registry.deps_of(name)
        if enrich is None:
            continue
        try:
            enrich(Deps())
        except Exception as exc:
            raise ConfigError(
                f"task type {name!r}: its deps factory cannot build a run's "
                f"Deps — {type(exc).__name__}: {exc}"
            ) from exc


def register_dags(
    config: Any,
    *,
    servers: dict[str, Any] | None = None,
    skills: Any = None,
    #: Where a node's model call is recorded — the same sink the extractors
    #: and the responder are built with. `None` records nothing.
    record: Any = None,
    #: The store the workflow adapter rebuilds each run's `Deps` from, and
    #: writes `node_runs` through. `None` registers the graphs for the pool to
    #: read (`dag_for`) but not on the DBOS adapter — every test that drives a
    #: node directly with a stub rather than running the durable workflow.
    db: Any = None,
) -> None:
    """Register every graph this build knows about, from the registry.

    Each task type registers itself (`friday.kernel.dag.task_types.register_all`); this
    reads the registry and maps each type to its graph. Nothing here names a
    type — adding one is registering a `TaskTypeSpec`, not editing this function.

    Idempotent: `register_all` clears the registry and `EDGE_ROUTER` starts from
    empty, so the "refuses to overwrite" guard stays live for everyone else
    rather than being disarmed at each call site.

    `dag_for` never answers "no graph" for a type this covers, which is every
    classifiable type there is.
    """
    from friday.kernel.dag.task_types import BootContext, register_all

    register_all(
        BootContext(
            config=config,
            record=record,
            servers=dict(servers or {}),
            skills=skills,
        )
    )

    EDGE_ROUTER.clear()
    for task_type in registry.specs():
        register_dag(task_type, registry.dag_of(task_type))

    # Register the same graphs on the DBOS adapter (ticket 06), the way the pool
    # runs them past node 0. The adapter rebuilds each run's `Deps` from a
    # serializable scope key inside the workflow — a live source cannot cross a
    # step boundary — so it reads the task back from the store and the task
    # type's own `deps` factory (ticket 13) enriches it with the type's handles.
    if db is not None:
        _register_on_adapter(db)


def _register_on_adapter(db: Any) -> None:
    from dataclasses import asdict

    from friday.kernel.dag import adapter
    from friday.sdk.workflow import Deps, NodeRun

    async def deps_factory(scope_key: dict[str, Any]) -> Deps:
        task_id = scope_key.get("task_id")
        task = await db.task(task_id) if task_id is not None else None
        # The kernel builds the base `Deps` from the run's serializable scope
        # key; the task type's own factory (ticket 13) enriches it with its typed
        # handles. A type with none takes the base as-is.
        base = Deps(task=task, db=db, servers=dict(registry.SERVERS))
        enrich = registry.deps_of(scope_key.get("task_type", ""))
        return enrich(base) if enrich is not None else base

    def recorder_factory(scope_key: dict[str, Any]):
        task_id = scope_key.get("task_id")

        async def record(run: NodeRun) -> None:
            await db.record_node_run(task_id=task_id, **asdict(run))

        return record

    adapter.clear_graphs()
    for dag in EDGE_ROUTER.values():
        adapter.register_graph(dag, deps_factory, recorder_factory=recorder_factory)
