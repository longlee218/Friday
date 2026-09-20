"""The one graph with an investigation past node 0.

`Prepare → Resolve → FindRequestLog → ReadFailingCode → Diagnose → Report`,
which is the operator's own routine with the parts they said Friday may not
do left out. Ticket 00 calls it a slice rather than the graph: the thinnest
line that reaches a real diagnosis through the real pool, outbox and board,
built to be run on five past cases and thrown away if the shape proves wrong.

**Every node declares itself here and owns its own module.** The router says
which graph runs which task type and nothing else — it does not know any node
name, which is what lets this package change shape without touching it.

**What is deliberately missing**, and which ticket owns it: `Notify` and
`Explain` and the reporter's brief (06), the Collector and its evidence set
(15), the dossier token budget (16's measurement, replaced here by fixed line
caps), memory writes and the guarded step to the database (05).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from friday.dag.api_issue.code import read_failing_code_node
from friday.dag.api_issue.diagnose import diagnose_node
from friday.dag.api_issue.logs import find_request_log_node
from friday.dag.api_issue.report import report_node
from friday.dag.api_issue.resolve import resolve_node
from friday.dag.engine import DAG, DAGState, Edge, status_of
from friday.dag.prepare import prepare_node
from friday.sources.logs import LokiSource, SshKubectlSource

__all__ = ["build_api_issue_dag", "build_diagnose_harness", "build_log_sources"]

log = logging.getLogger(__name__)

TASK_TYPE = "api_issue"

#: What a node that reads the world is given before it is called stuck. Not
#: a model call, so these are network round trips, not thinking: ticket 16
#: measured dev at ~1.5 s a round trip and this node makes two.
LOG_TIMEOUT_SECONDS = 90.0
CODE_TIMEOUT_SECONDS = 30.0


def _ran_ok(node: str):
    """Follow this edge only when `node` produced a usable envelope.

    Written as a predicate rather than left to each node to re-check, because
    a node reading an earlier node's hand-over as if it were a result is the
    wiring mistake `DAGState` raises on — and the fix for that is in the
    edges, which is here.
    """

    def when(state: DAGState) -> bool:
        return status_of(state.get(node)) == "ok"

    return when


def build_api_issue_dag(
    *,
    extractor: Any = None,
    diagnose: Any = None,
    diagnose_harness: Any = None,
    budget_tokens: int | None = None,
    reports_dir: Path | None = None,
) -> DAG:
    """The six nodes and the edges between them.

    `on_ready` is `None` on node 0 — that is the whole difference between a
    type with an investigation and a type without one. A complete set of
    parameters is not an answer here; it is the start of the work, and
    `prepare` returns the parameters for `Resolve` to read.

    `extractor` and `diagnose` are `AgentConfig`s or `None`. `None` is a
    fresh install: node 0 falls back to code alone, and `Diagnose` skips with
    a reason rather than the graph refusing to build. A graph that cannot be
    registered without a model is a graph nobody can run a test against.
    """
    from friday.dag.router import NODE_CLOCK_MARGIN_SECONDS

    return DAG(
        name=TASK_TYPE,
        nodes=(
            prepare_node(
                TASK_TYPE,
                _params_cls(),
                on_ready=None,
                budget_tokens=budget_tokens,
                agent=None if extractor is None else "extractor",
                timeout_seconds=(
                    None
                    if extractor is None
                    else extractor.timeout_seconds + NODE_CLOCK_MARGIN_SECONDS
                ),
            ),
            resolve_node(),
            find_request_log_node(timeout_seconds=LOG_TIMEOUT_SECONDS),
            read_failing_code_node(timeout_seconds=CODE_TIMEOUT_SECONDS),
            diagnose_node(
                harness=diagnose_harness,
                agent=None if diagnose is None else "diagnose",
                timeout_seconds=(
                    None
                    if diagnose is None
                    else diagnose.timeout_seconds + NODE_CLOCK_MARGIN_SECONDS
                ),
            ),
            report_node(reports_dir=reports_dir or _default_reports_dir()),
        ),
        edges=(
            # Node 0 runs outside the walk (`Pool._run_dag`), and an `Ask` or
            # a `HandOver` from it ends the pass before any edge is read — so
            # this one is unconditional by construction, not by hope.
            Edge("prepare", "resolve"),
            # `Resolve` hands over on an external domain or a missing row.
            # Nothing past it can run without a placement.
            Edge("resolve", "find_request_log", when=_ran_ok("resolve")),
            # The three after it always follow: a skipped log node still has
            # a report to write, and saying "nothing was read" is the output
            # the deleted graph never produced.
            Edge("find_request_log", "read_failing_code"),
            Edge("read_failing_code", "diagnose"),
            Edge("diagnose", "report"),
        ),
    )


def build_log_sources(config: Any, servers: dict[str, Any]) -> dict[str, Any]:
    """The two ways to read a log, from configuration (D4).

    A source that is not configured is simply absent, and the node says which
    one it wanted. Neither is built speculatively: an `SshKubectlSource`
    pointing at a host that does not resolve would turn every dev task into a
    failed subprocess, which reads like a broken graph rather than an unset
    option.
    """
    settings = getattr(config, "api_issue", None)
    if settings is None:
        return {}

    sources: dict[str, Any] = {}
    if settings.ssh_host:
        sources["kubectl"] = SshKubectlSource(host=settings.ssh_host)
    server = servers.get(settings.loki_server)
    if server is not None:
        sources["loki"] = LokiSource(server=server, tool=settings.loki_tool)
    if not sources:
        log.info(
            "api_issue: no log source configured — set api_issue.ssh_host for "
            "dev, or an mcp_servers entry named %r for production",
            settings.loki_server,
        )
    return sources


def build_diagnose_harness(config: Any, *, record: Any = None, spent: Any = None) -> Any:
    """The model behind `Diagnose`, or `None` when no `diagnose` agent is
    configured.

    Here rather than in the router because this package owns its own agents:
    the router says which graph runs which task type and nothing else. Built
    once, not per call, for the reason every other harness is — it holds a
    client, and a client has a lifetime.

    `None` is a supported state, not a degraded one. `config.yaml` ships the
    block, and an operator who comments it out gets a graph that still reads
    logs, still reads code and still writes a report saying nothing was
    diagnosed and why.
    """
    agent = config.agents.get("diagnose")
    if agent is None:
        return None

    from friday.agent.harness import Harness
    from friday.dag.api_issue.diagnose import Diagnosis
    from friday.dag.api_issue.prompt import build_instructions

    return Harness(
        config=agent,
        instructions=build_instructions(),
        answers=Diagnosis,
        record=record,
        spent=spent,
    )


def _default_reports_dir() -> Path:
    """Where reports go when the caller named nowhere — the configuration's
    own default, read off the dataclass rather than written down again."""
    from friday.config import ApiIssueConfig

    return Path(ApiIssueConfig().reports_dir)


def _params_cls() -> Any:
    from friday.domain.models import PARAMS

    return PARAMS[TASK_TYPE]
