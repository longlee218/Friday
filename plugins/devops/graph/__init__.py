"""The `devops.api_issue` graph — the one type with an investigation past node 0.

`Prepare → Resolve → FindRequestLog → ReadFailingCode → Diagnose → Report`,
which is the operator's own routine with the parts they said Friday may not do
left out.

**Built against the sdk, and the caps the composition root hands in.** A plugin
imports `friday.sdk` only, so this builder never reaches for the kernel's
`prepare_node` or the `Harness` class: it asks `caps.prepare_node(...)` for
node 0 and `caps.make_harness(...)` for the model behind `Diagnose`. `caps` also
carries the tool servers a run opened and the `sender`/`approver` identities a
queued row uses. Ticket 14 lifted this package out of `friday/kernel/dag/api_issue/`
into `plugins/devops/` unchanged in shape, only in where it reaches for things.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from friday.sdk.sources import Reads
from friday.sdk.workflow import (
    DAG,
    DAGState,
    Edge,
    NODE_CLOCK_MARGIN_SECONDS,
    Ask,
    HandOver,
    Reply,
    status_of,
)
from plugins.devops.graph.acknowledge import acknowledge_node
from plugins.devops.graph.code import read_failing_code_node
from plugins.devops.graph.diagnose import (
    Diagnosis,
    ask_reporter,
    diagnose_node,
    hand_over,
)
from plugins.devops.graph.logs import find_request_log_node
from plugins.devops.graph.prompt import build_instructions
from plugins.devops.graph.report import report_node
from plugins.devops.graph.resolve import resolve_node
from plugins.devops.params import ApiIssueParams
from plugins.devops.sources.logs import LokiSource, SshKubectlSource
from plugins.devops.sources.release import ReleaseSource

__all__ = [
    "TASK_TYPE",
    "build_devops_dag",
    "build_log_sources",
    "build_release_source",
]

log = logging.getLogger(__name__)

TASK_TYPE = "devops.api_issue"

#: The three nodes that only read a row or write one. **Bounded because nothing
#: is**, not because they are slow: `resolve`, `acknowledge` and `report` all
#: reach the database, and a node with no ceiling waits on a hung connection for
#: as long as the process lives — holding a pool slot. Generous enough that a
#: busy SQLite write never trips it, small enough to be a bound.
ROW_TIMEOUT_SECONDS = 20.0

LOG_TIMEOUT_SECONDS = 90.0
CODE_TIMEOUT_SECONDS = 30.0


def _ran_ok(node: str):
    """Follow this edge only when `node` produced a usable envelope."""

    def when(state: DAGState) -> bool:
        return status_of(state.get(node)) == "ok"

    return when


def _did_not_decide(node: str):
    """Follow this edge only when `node` returned an envelope to carry on with,
    not an `Action` that already decides the task.

    A node that hands over (or asks, or replies) has decided the case; the walk
    must **stop there** so the pool reads that `Action` off the state — exactly
    as the adapter's own note says a `HandOver` "flows on as a terminal result".
    An ungated edge here would fall through to `Report`, which sees a non-dict
    in the diagnose slot, produces its *own* generic hand-over, and discards the
    model's reason. Unlike `_ran_ok`, an `empty`/`error` envelope still advances:
    `Report` is what turns those into the operator's brief."""

    def when(state: DAGState) -> bool:
        return not isinstance(state.get(node), (Ask, HandOver, Reply))

    return when


def build_devops_dag(api: Any) -> DAG:
    """The whole graph, built from the plugin's config and the composition
    root's boot capabilities (`api.caps`).

    Node 0 is `caps.prepare_node` — the same extractor node every task type
    runs, reached through caps so this plugin imports none of the kernel's
    graph machinery. `on_ready` is `None` on it: a complete set of parameters
    is the *start* of the work here, not an answer, and `prepare` returns the
    parameters for `Resolve` to read.

    The diagnose model is `caps.make_harness`, `None` when no `devops.diagnose`
    agent is configured — a fresh install, and every test that does not set one
    up. The node then skips, with a reason, and the graph still reaches `Report`.
    """
    caps = api.caps
    cfg = api.config  # DevopsConfig (the plugin's own block)
    whole = caps.config  # the full application Config, for shared agents/budget
    extractor = whole.agents.get("extractor")
    diagnose_agent = whole.agents.get("devops.diagnose")
    budget_tokens = whole.context.extraction_budget_tokens

    diagnose_harness = caps.make_harness(
        agent=diagnose_agent,
        instructions=build_instructions(reads=False),
        answers=Diagnosis,
    )
    # A factory, because under v3.3 the tools carry this run's placement and
    # numbering — one harness built at boot would read the previous case's
    # service.
    make_diagnose_harness = (
        (lambda *, tools: caps.make_harness(
            agent=diagnose_agent,
            instructions=build_instructions(reads=bool(tools)),
            answers=Diagnosis,
            tools=tools,
            # The model may finish by handing the case to the operator instead
            # of answering — a terminal output tool beside the answer shape.
            ends_with=[hand_over, ask_reporter],
        ))
        if cfg.diagnose_reads
        else None
    )

    return DAG(
        name=TASK_TYPE,
        nodes=(
            caps.prepare_node(
                TASK_TYPE,
                ApiIssueParams,
                on_ready=None,
                budget_tokens=budget_tokens,
                agent=None if extractor is None else "extractor",
                # **A ceiling either way.** With no extractor configured this
                # node makes no model call and is fast by construction — but
                # fast by construction is not bounded. The agent's clock when
                # there is one, the row clock when there is not.
                timeout_seconds=(
                    ROW_TIMEOUT_SECONDS
                    if extractor is None
                    else extractor.timeout_seconds + NODE_CLOCK_MARGIN_SECONDS
                ),
            ),
            resolve_node(timeout_seconds=ROW_TIMEOUT_SECONDS),
            acknowledge_node(timeout_seconds=ROW_TIMEOUT_SECONDS),
            find_request_log_node(timeout_seconds=LOG_TIMEOUT_SECONDS),
            read_failing_code_node(timeout_seconds=CODE_TIMEOUT_SECONDS),
            diagnose_node(
                harness=diagnose_harness,
                make_harness=make_diagnose_harness,
                agent=None if diagnose_agent is None else "devops.diagnose",
                timeout_seconds=(
                    ROW_TIMEOUT_SECONDS
                    if diagnose_agent is None
                    else diagnose_agent.timeout_seconds + NODE_CLOCK_MARGIN_SECONDS
                ),
            ),
            report_node(
                reports_dir=Path(cfg.reports_dir),
                timeout_seconds=ROW_TIMEOUT_SECONDS,
            ),
        ),
        edges=(
            Edge("prepare", "resolve"),
            Edge("resolve", "acknowledge", when=_ran_ok("resolve")),
            Edge("acknowledge", "find_request_log"),
            Edge("find_request_log", "read_failing_code"),
            Edge("read_failing_code", "diagnose"),
            # Gated: a `HandOver` from the reads loop is the decision, so the
            # walk stops at diagnose rather than falling through to `report`
            # (which would replace the model's reason with its own).
            # Gated: a `HandOver` from the reads loop is the decision, so the
            # walk stops at diagnose rather than falling through to `report`
            # (which would replace the model's reason with its own).
            Edge("diagnose", "report", when=_did_not_decide("diagnose")),
        ),
    )


def build_log_sources(cfg: Any, servers: dict[str, Any]) -> dict[str, Any]:
    """The two ways to read a log, from configuration (D4).

    A source that is not configured is simply absent, and the node says which
    one it wanted. Neither is built speculatively: an `SshKubectlSource`
    pointing at a host that does not resolve would turn every dev task into a
    failed subprocess, which reads like a broken graph rather than an unset
    option.
    """
    sources: dict[str, Any] = {}
    if cfg.ssh_host:
        sources["kubectl"] = SshKubectlSource(host=cfg.ssh_host)
    server = servers.get(cfg.loki_server)
    if server is not None:
        # Narrowed here, not inside the source: the source declares what it
        # calls, and this is where a server meets that declaration.
        sources["loki"] = LokiSource(
            server=Reads(server, LokiSource.TOOLS), tool=cfg.loki_tool
        )
    if not sources:
        log.info(
            "devops: no log source configured — set devops.ssh_host for dev, "
            "or an mcp_servers entry named %r for production",
            cfg.loki_server,
        )
    return sources


def build_release_source(cfg: Any, servers: dict[str, Any]) -> Any:
    """What the cluster says it is running, or `None`.

    The same server the Loki tools come from — it carries both — and narrowed
    the same way: `Reads` over what `ReleaseSource` declares.
    """
    server = servers.get(cfg.loki_server)
    if server is None:
        return None
    return ReleaseSource(server=Reads(server, ReleaseSource.TOOLS))
