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
from pathlib import Path
from typing import Any

from friday.config import ConfigError
from friday.dag.engine import DAG
from friday.dag.prepare import plan_by_required_parameters, prepare_node
from friday.domain.models import PARAMS, Params

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


def _budgets(config: Any) -> dict[str, float]:
    """How long one task of each type may hold a pool slot.

    One entry today. A graph with no budget is not checked, which is the
    honest default for the one-node graphs: their single node's clock *is*
    the bound.
    """
    # Imported here rather than at the top, the way the rest of this module
    # reaches the graph packages: they import back from it.
    from friday.dag.api_issue import TASK_TYPE as API_ISSUE

    api_issue = getattr(config, "api_issue", None)
    clock = getattr(api_issue, "timeout_seconds", None)
    return {} if clock is None else {API_ISSUE: float(clock)}


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


def _graphs(config: Any, *, diagnose_harness: Any = None) -> dict[str, DAG]:
    """Every type's graph, built from the configuration.

    One of them is not the one-node graph: `api_issue` has an investigation
    past node 0 again (ticket 00). Which nodes those are is that package's
    business — this function asks it for a graph and registers what it gets.
    """
    from friday.dag.api_issue import TASK_TYPE as API_ISSUE, build_api_issue_dag

    budget_tokens = config.context.extraction_budget_tokens
    extractor = config.agents.get("extractor")
    settings = getattr(config, "api_issue", None)
    graphs = {
        task_type: build_simple_dag(
            task_type,
            params_cls,
            budget_tokens=budget_tokens,
            extractor=extractor,
        )
        for task_type, params_cls in PARAMS.items()
        if task_type != API_ISSUE
    }
    if API_ISSUE in PARAMS:
        graphs[API_ISSUE] = build_api_issue_dag(
            extractor=extractor,
            diagnose=config.agents.get("diagnose"),
            diagnose_harness=diagnose_harness,
            budget_tokens=budget_tokens,
            reports_dir=(
                None if settings is None else Path(settings.reports_dir)
            ),
        )
    return graphs


def check_graphs(config: Any) -> None:
    """Refuse a configuration under which a model node's clock would cut its
    harness short — from the configuration alone, so the composition root
    runs it straight after `load_config`, before it opens the database or
    builds anything a graph is later handed. `register_dags` checks again
    what it actually registers."""
    check_node_clocks(_graphs(config).values(), config.agents)
    check_graph_clocks(_graphs(config).values(), _budgets(config))


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
) -> None:
    """Register every graph this build knows about.

    Idempotent, and it says so once here rather than by disarming
    `register_dag`'s guard at each call. It used to `pop` the key immediately
    before every registration, which meant the "refuses to overwrite" check
    could not fire anywhere but in a test — a guard deleted at every call site
    that mattered (ticket 13). Starting from empty states the same intention
    and leaves the guard live for everyone else.

    Every entry in `PARAMS` gets the same one-node graph except `api_issue`,
    which has an investigation past node 0 (ticket 00).

    There was a five-node `api_issue` graph before this one — read the logs,
    find the code, analyse, propose a patch, compose a reply — and the
    operator removed it: "a workflow nobody had described, built from a guess
    at what investigating an API fault looks like, and every node of it
    skipped on every run because no tool server was ever configured". The
    graph registered here answers both halves of that. It is a transcription
    of the operator's own routine, taken one question at a time; and a node
    with nothing to read skips **out loud** — an envelope with a reason,
    rendered on the board and carried into the report — rather than silently.

    It is still a slice, and it is allowed to be thrown away: five past cases
    are what decide whether the shape is right.

    `dag_for` never answers "no graph" for a type this covers, which is every
    classifiable type there is. Build a multi-node graph when there are steps
    worth skipping and somebody has said what they are.
    """
    from friday.outbox import DEFAULT_APPROVER, DEFAULT_SENDER
    from friday.dag.api_issue import (
        TASK_TYPE as API_ISSUE,
        build_diagnose_harness,
        build_log_sources,
    )

    EDGE_ROUTER.clear()
    for task_type, dag in _graphs(
        config,
        diagnose_harness=build_diagnose_harness(config, record=record, spent=spent),
    ).items():
        register_dag(task_type, dag)

    check_node_clocks(EDGE_ROUTER.values(), config.agents)
    check_graph_clocks(EDGE_ROUTER.values(), _budgets(config))

    # Node 0 reaches its extractor through `friday.extraction`'s registry,
    # not through here, and `Diagnose` is handed its harness when the graph is
    # built. What travels in `DAG_DEPS_EXTRA` is what a *run* needs and a
    # graph cannot hold: the log sources, which wrap connections this process
    # opened and closes.
    DAG_DEPS_EXTRA.clear()
    # The account the graph's own rows are sent from. It is the pool's
    # `sender` — the watched account — and the graph needs it because two of
    # its three outputs are queued mid-run, before any action reaches the
    # pool that would have known it.
    # **Two identities, and which one a row carries decides who reads it.**
    # `sender` posts into the reporter's channel as the watched account;
    # `approver` direct-messages the operator. The graph needs both because
    # it queues rows for both, mid-run, before any action reaches the pool.
    extra: dict[str, Any] = {
        "sender": DEFAULT_SENDER, "approver": DEFAULT_APPROVER,
    }
    sources = build_log_sources(config, dict(servers or {}))
    if sources:
        extra["log_sources"] = sources
    DAG_DEPS_EXTRA[API_ISSUE] = extra
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
