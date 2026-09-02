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

from friday.dag import DAG
from friday.dag import api_issue as graph_names
from friday.dag.api_issue import build_api_issue_dag
from friday.dag.prepare import prepare_node
from friday.domain.models import Params

__all__ = [
    "EDGE_ROUTER",
    "agents_for_api_issue",
    "build_simple_dag",
    "dag_for",
    "register_dag",
    "register_dags",
]

log = logging.getLogger(__name__)

#: Task type -> the DAG that runs it. Populated by `register_dags` at
#: startup; read by `WorkflowRunner._plan`.
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
    covered is a wiring bug — `WorkflowRunner._plan` asserts on it rather
    than falling back to a second way of deciding what to do."""
    return EDGE_ROUTER.get(task_type)


# --- the one-node graph, for a type with no investigation --------------------


def build_simple_dag(task_type: str, params_cls: type[Params]) -> DAG:
    """`prepare`, then ask for what is missing or hand the rest over — what
    every type without an investigation needs (D1). There is nothing here
    worth a second node yet; build one when there are steps worth skipping,
    not before.
    """
    from friday.workflows import plan_by_required_parameters

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


# --- building the agents behind a graph's nodes -----------------------------

#: Which config block builds the agent for each `api_issue` node. A node whose
#: block is absent runs without an agent, which every node in that graph is
#: written to survive — it skips rather than fails.
_API_ISSUE_AGENTS = {
    "read_logs": "dag_read_logs",
    "find_code_path": "dag_find_code",
    "analyze_stack": "dag_analyze",
    "fix_bug": "dag_fix",
    "compose_reply": "dag_compose",
}


def agents_for_api_issue(
    config: Any, skills: Any = None, servers: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build one agent per node that has a configuration block.

    Wiring only: which config block, which tool server, which tools. What the
    prompt says and how it is assembled is `friday.dag.prompt`'s business, and
    the per-node texts live in `prompts/dag/`.
    """
    from friday.agent.harness import Harness
    from friday.agent.skills import fetch_skill_tool
    from friday.dag.prompt import REASONING, build_instructions

    built: dict[str, Any] = {}
    for node, block in _API_ISSUE_AGENTS.items():
        agent_config = config.agents.get(block)
        if agent_config is None:
            continue
        wants_skills = skills is not None and node in REASONING
        tools = [fetch_skill_tool(skills)] if wants_skills else []
        available = servers or {}
        wanted = graph_names.NODE_SERVERS.get(node)
        mcp = [available[wanted]] if wanted in available else []
        built[node] = Harness(
            config=agent_config,
            instructions=build_instructions(
                node,
                persona=getattr(config, "persona", None),
                skills=skills if wants_skills else None,
            ),
            tools=tools,
            mcp_servers=mcp,
        )
    return built


def register_dags(
    config: Any,
    *,
    servers: dict[str, Any] | None = None,
    skills: Any = None,
    context_store: Any = None,
) -> None:
    """Register every graph this build knows about.

    Idempotent: re-registering the same task type replaces it rather than
    raising, because the composition root may run twice in a test process and
    a second startup is not a wiring mistake.

    Every entry in `PARAMS` gets a graph: `api_issue` its own, everything else
    the one-node graph `build_simple_dag` builds. `dag_for` never answers "no
    graph" for a type this covers, which is every classifiable type there is.
    """
    from friday.workflows import PARAMS

    EDGE_ROUTER.pop("api_issue", None)
    register_dag("api_issue", build_api_issue_dag())

    for task_type, params_cls in PARAMS.items():
        if task_type == "api_issue":
            continue
        EDGE_ROUTER.pop(task_type, None)
        register_dag(task_type, build_simple_dag(task_type, params_cls))

    agents = agents_for_api_issue(config, skills, servers)
    if agents:
        log.info("api_issue graph: agents for %s", ", ".join(sorted(agents)))
        # Which servers are *missing* — otherwise "why did read_logs never
        # look anything up" has no answer anywhere. The node skips correctly;
        # it just skips silently.
        absent = sorted(
            {
                server
                for node, server in graph_names.NODE_SERVERS.items()
                if node in agents and server not in (servers or {})
            }
        )
        if absent:
            log.info(
                "api_issue graph: no %s server — the nodes needing it will skip",
                ", ".join(absent),
            )
    else:
        log.info(
            "api_issue graph: no node agents configured — it will park every "
            "report rather than investigate one"
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


#: Node name -> agent, per task type. Read by `WorkflowRunner` when it builds
#: `DAGDeps`; empty until `register_dags` runs.
DAG_DEPS_EXTRA: dict[str, dict[str, Any]] = {}

#: Tool servers available to every graph, by name.
DAG_SERVERS: dict[str, Any] = {}
