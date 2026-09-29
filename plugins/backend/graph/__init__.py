"""The `backend.trace_problem` graph — the one type with an investigation past node 0.

`Intake → Acknowledge → Diagnose → Report` (ticket 06). `Prepare`/`Resolve` —
the extractor and the table-lookup node that used to sit ahead of
`Acknowledge` — are gone for this task type: `Intake` folds both into one
deterministic node (ticket 03), so there is no extraction pass and no
separate resolve step left to run. The two fixed pre-fetch nodes the diagram
used to name here — `FindRequestLog`, `ReadFailingCode` — were already gone
(ticket 05): `Diagnose` reads the log and the code itself, through
the `backend.logs` and `backend.code` toolsets, so there is nothing left to fetch in
advance of it.

**Built against the sdk, and the caps the composition root hands in.** A plugin
imports `friday.sdk` only, so this builder never reaches for the kernel's
`Harness` class directly: it asks `caps.make_harness(...)` for the model
behind `Diagnose`. `caps` also carries the tool servers a run opened and the
`sender`/`approver` identities a queued row uses. Ticket 14 lifted this
package out of the kernel's DAG folder into `plugins/backend/` unchanged
in shape, only in where it reaches for things.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from friday.sdk.agent import AgentDeclaration
from friday.sdk.workflow import (
    DAG,
    Ask,
    DAGState,
    Edge,
    HandOver,
    Reply,
    status_of,
)
from plugins.backend.graph.acknowledge import acknowledge_node
from plugins.backend.graph.diagnose import (
    Diagnosis,
    ask_reporter,
    diagnose_node,
    hand_over,
)
from plugins.backend.graph.intake import intake_node
from plugins.backend.graph.prompt import build_instructions
from plugins.backend.graph.report import REPORTS_DIR, report_node

__all__ = ["TASK_TYPE", "build_backend_dag"]

TASK_TYPE = "backend.trace_problem"

#: The graph's one model node. Reads logs and code, so the widest token
#: budget; one turn plus its correction, what it had before turns counted tool
#: calls (board `domains-plug-in`, ticket 17 — the loop's real budget comes
#: with the spine). 60s a request: it reads logs and code, the largest prompt.
DIAGNOSE = AgentDeclaration(
    name="backend.diagnose",
    tier="flash",
    temperature=0.0,
    max_turns=1,
    tokens=500_000,
    request_timeout_seconds=60.0,
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


def build_backend_dag(
    api: Any, *, toolsets: Any = None, reports_dir: Path = REPORTS_DIR
) -> DAG:
    """The whole graph, built from the composition root's boot capabilities
    (`api.caps`).

    Node 0 is `intake_node(caps.intake)` — core Intake, deterministic, no
    model: it reads the task's own text rather than a set of extracted
    parameters, so there is nothing here for `caps.prepare_node` to fill in
    first.

    `Diagnose`'s tools come from the plugin's own toolsets (`backend.logs`,
    `backend.code`), built per run by `caps.build_tools` — the core's door
    that narrows each server to what the toolset declared (build-the-spine
    ticket 09). `toolsets` replaces them: a replay hands in `backend.logs`
    reading a captured case.

    The diagnose model is `caps.make_harness`. In production `whole.agent`
    always resolves `DIAGNOSE` (an undeclared tier refuses the boot); it is
    `None` only where a caller hides it — `replay_case.py` without a model, and
    tests. The node then skips, with a reason, and the graph still reaches
    `Report`.
    """
    # Here, not at the top: `toolsets.logs` imports `graph.distil`, so a
    # module-level import would be a cycle.
    from plugins.backend.toolsets import CODE, LOGS

    caps = api.caps
    whole = caps.config  # the full application Config, for the shared agent
    diagnose_agent = whole.agent(DIAGNOSE)
    granted = (LOGS, CODE) if toolsets is None else tuple(toolsets)

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
            intake_node(api.caps.intake),
            acknowledge_node(sender=caps.sender),
            diagnose_node(
                make_harness=make_diagnose_harness,
                build_tools=lambda run: caps.build_tools(granted, run),
                agent=None if diagnose_agent is None else "backend.diagnose",
            ),
            report_node(reports_dir=Path(reports_dir), approver=caps.approver),
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
