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


def agents_for_api_issue(config: Any, skills: Any = None) -> dict[str, Any]:
    """Build one agent per node that has a configuration block.

    Instructions come from the graph module, so the prompt lives beside the
    node that sends it. Configuration supplies only where the model is and
    what it costs.
    """
    from friday.dag import api_issue as graph
    from friday.harness import Harness
    from friday.skills import fetch_skill_tool

    instructions = {
        "read_logs": graph.READ_LOGS,
        "find_code_path": graph.FIND_CODE,
        "analyze_stack": graph.ANALYZE,
        "fix_bug": graph.FIND_CODE,
        "compose_reply": graph.COMPOSE,
    }

    # The nodes that reason get the skill library; the ones that only call a
    # tool do not. "How to trace a request" is written down for whoever is
    # deciding what the logs mean, not for the thing fetching them.
    reasoning = {"analyze_stack", "compose_reply"}

    built: dict[str, Any] = {}
    for node, block in _API_ISSUE_AGENTS.items():
        agent_config = config.agents.get(block)
        if agent_config is None:
            continue
        tools = (
            [fetch_skill_tool(skills)]
            if skills is not None and node in reasoning
            else []
        )
        built[node] = Harness(
            config=agent_config,
            instructions=instructions[node] + _skills_block(skills, node, reasoning),
            tools=tools,
        )
    return built


def _skills_block(skills: Any, node: str, reasoning: set[str]) -> str:
    """The catalogue, appended to a reasoning node's instructions.

    In the instructions rather than the per-call bundle because it is the
    stable part: the same list every call, so it costs one cache entry rather
    than one per task.
    """
    if skills is None or node not in reasoning or not len(skills):
        return ""
    lines = [
        "",
        "",
        "Skills you can read in full with fetch_skill(name):",
    ]
    lines += [f"- {line}" for line in skills.catalogue()]
    return "\n".join(lines)


def register_dags(
    config: Any,
    *,
    servers: dict[str, Any] | None = None,
    skills: Any = None,
) -> None:
    """Register every graph this build knows about.

    Idempotent: re-registering the same task type replaces it rather than
    raising, because the composition root may run twice in a test process and
    a second startup is not a wiring mistake.
    """
    EDGE_ROUTER.pop("api_issue", None)
    register_dag("api_issue", build_api_issue_dag())

    agents = agents_for_api_issue(config, skills)
    if agents:
        log.info("api_issue graph: agents for %s", ", ".join(sorted(agents)))
    else:
        log.info(
            "api_issue graph: no node agents configured — it will ask for a "
            "correlationId rather than investigate"
        )

    # Stored for the runner to hand down through `DAGDeps`. Kept here rather
    # than closed over inside the graph so the graph stays testable without
    # either a model or a tool server.
    DAG_DEPS_EXTRA["api_issue"] = agents
    DAG_SERVERS.update(servers or {})


#: Node name -> agent, per task type. Read by `WorkflowRunner` when it builds
#: `DAGDeps`; empty until `register_dags` runs.
DAG_DEPS_EXTRA: dict[str, dict[str, Any]] = {}

#: Tool servers available to every graph, by name.
DAG_SERVERS: dict[str, Any] = {}
