"""The `devops.api_issue` graph — the one type with an investigation past node 0.

`Intake → Acknowledge → Diagnose → Report` (ticket 06). `Prepare`/`Resolve` —
the extractor and the table-lookup node that used to sit ahead of
`Acknowledge` — are gone for this task type: `Intake` folds both into one
deterministic node (ticket 03), so there is no extraction pass and no
separate resolve step left to run. The two fixed pre-fetch nodes the diagram
used to name here — `FindRequestLog`, `ReadFailingCode` — were already gone
(ticket 05): `Diagnose` reads the log and the code itself, through
`plugins.devops.investigate`'s tools, so there is nothing left to fetch in
advance of it.

**Built against the sdk, and the caps the composition root hands in.** A plugin
imports `friday.sdk` only, so this builder never reaches for the kernel's
`Harness` class directly: it asks `caps.make_harness(...)` for the model
behind `Diagnose`. `caps` also carries the tool servers a run opened and the
`sender`/`approver` identities a queued row uses. Ticket 14 lifted this
package out of `friday/kernel/dag/api_issue/` into `plugins/devops/` unchanged
in shape, only in where it reaches for things.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from friday.sdk.agent import AgentDeclaration
from friday.sdk.sources import Reads
from friday.sdk.workflow import (
    DAG,
    DAGState,
    Edge,
    Ask,
    HandOver,
    Reply,
    status_of,
)
from plugins.devops.graph.acknowledge import acknowledge_node
from plugins.devops.graph.diagnose import (
    Diagnosis,
    ask_reporter,
    diagnose_node,
    hand_over,
)
from plugins.devops.graph.intake import intake_node
from plugins.devops.graph.prompt import build_instructions
from plugins.devops.graph.report import report_node
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

#: The graph's one model node. Reads logs and code, so the widest token
#: budget; one turn plus its correction, what it had before turns counted tool
#: calls (board `domains-plug-in`, ticket 17 — the loop's real budget comes
#: with the spine). 60s a request: it reads logs and code, the largest prompt.
DIAGNOSE = AgentDeclaration(
    name="devops.diagnose", tier="flash", temperature=0.0, max_turns=1,
    tokens=500_000, request_timeout_seconds=60.0,
)


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

    Node 0 is `intake_node()` — deterministic, no model, no `caps` involved:
    it reads the task's own text rather than a set of extracted parameters,
    so there is nothing here for `caps.prepare_node` to fill in first.

    The diagnose model is `caps.make_harness`. In production `whole.agent`
    always resolves `DIAGNOSE` (an undeclared tier refuses the boot); it is
    `None` only where a caller hides it — `replay_case.py` without a model, and
    tests. The node then skips, with a reason, and the graph still reaches
    `Report`.
    """
    caps = api.caps
    cfg = api.config  # DevopsConfig (the plugin's own block)
    whole = caps.config  # the full application Config, for the shared agent
    diagnose_agent = whole.agent(DIAGNOSE)

    # A factory, because the tools carry this run's placement and numbering —
    # one harness built at boot would read the previous case's service.
    # `caps.make_harness` is itself `None` when `diagnose_agent` is `None`
    # (replay without a model, tests), so this needs no separate gate.
    make_diagnose_harness = lambda *, tools: caps.make_harness(
        agent=diagnose_agent,
        instructions=build_instructions(reads=bool(tools)),
        answers=Diagnosis,
        tools=tools,
        # The model may finish by handing the case to the operator instead
        # of answering — a terminal output tool beside the answer shape.
        ends_with=[hand_over, ask_reporter],
    )

    return DAG(
        name=TASK_TYPE,
        nodes=(
            intake_node(),
            acknowledge_node(),
            diagnose_node(
                make_harness=make_diagnose_harness,
                agent=None if diagnose_agent is None else "devops.diagnose",
            ),
            report_node(reports_dir=Path(cfg.reports_dir)),
        ),
        edges=(
            Edge("intake", "acknowledge", when=_ran_ok("intake")),
            Edge("acknowledge", "diagnose"),
            # Gated: an `Ask` or a `HandOver` from the reads loop is the
            # decision, so the walk stops at diagnose rather than falling
            # through to `report` (which would replace the model's reason
            # with its own).
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
