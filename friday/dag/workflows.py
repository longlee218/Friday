"""Which graphs exist, and what they are built from.

The composition root calls `register_dags(config, servers)` once at startup and
learns nothing about any individual graph — the same shape
`friday.extraction.register_extractors` uses, for the same reason. Adding a
workflow is an edit here, not there.

Registration is a function rather than a module-level side effect. Importing
`friday.dag.api_issue` to read it should not change what the process will do,
and a test that wants the deterministic path should be able to have it.
"""

from __future__ import annotations

import logging
from typing import Any

from friday.dag import api_issue as graph_names
from friday.dag.api_issue import build_api_issue_dag
from friday.dag.router import EDGE_ROUTER, register_dag

__all__ = ["agents_for_api_issue", "register_dags"]

log = logging.getLogger(__name__)

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
    """
    EDGE_ROUTER.pop("api_issue", None)
    register_dag("api_issue", build_api_issue_dag())

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
