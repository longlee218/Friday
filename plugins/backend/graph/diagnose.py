"""Node 4: say what caused it, and say what was not checked.

**The reads loop is the only diagnose mode** (ticket 05): the model fetches
its own evidence through the `backend.logs` and `backend.code` toolsets rather than
being handed a fixed dossier. The two fixed pre-fetch nodes that used to
build one — `FindRequestLog`, `ReadFailingCode` — are gone; `_reading` below
is what is left.

**The grounding gate** (`void_reason`) lives with the agent in
`plugins/backend/agents/diagnose.py` since build-the-spine ticket 14; this
node asks the same copy the spine does.

The spec has the *answer tool* refuse it, so the model can correct inside its
own turn budget (D8). The slice checks after the answer instead, and an
ungrounded answer is an envelope the graph hands over on rather than a reply.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from friday.sdk.toolset import tool
from friday.sdk.workflow import Ask, DAGState, Deps, HandOver, Node, envelope
from plugins.backend.agents.diagnose import (
    Diagnosis,
    quoted,
    unresolved_refs,
    void_reason,
)

__all__ = [
    "Diagnosis",
    "ask_reporter",
    "diagnose_node",
    "diagnosis_of",
    "hand_over",
    "unresolved_refs",
]


@tool
def ask_reporter(question: str) -> Ask:
    """Ask the reporter for something you need and cannot read yourself.

    Call this only when a missing piece of the report blocks the diagnosis and
    the reporter is the one who has it — a correlationId they did not paste, the
    exact request that failed, which environment they hit. Do NOT call it to
    hand the case to the operator (use `hand_over`), and do NOT call it merely
    because you are unsure — an unsure diagnosis with `conclusive: false` is more
    useful than a question. Unlike `hand_over`'s reason, `question` is sent to
    the reporter, so write it in their language and ask for exactly one thing.

    Args:
        question: what you need from the reporter, one sentence, in Vietnamese.
    """
    return Ask(text=question)


@tool
def hand_over(reason: str) -> HandOver:
    """Give up and hand this case to the operator instead of answering.

    Call this only when you genuinely cannot diagnose it and a person must: the
    fix needs an action you may not take, the evidence is out of reach, or the
    case is outside what these tools can investigate. Do NOT call it merely
    because you are unsure — an unsure diagnosis with `conclusive: false` is more
    useful than a hand-over. `reason` is read by the operator and is never sent
    to the reporter.

    Args:
        reason: why you are handing over, one or two sentences, for the operator.
    """
    return HandOver(reason=reason)


log = logging.getLogger(__name__)


def diagnosis_of(result: Any) -> Diagnosis | None:
    """Node 4's envelope read back as the shape the model answered.

    The envelope carries JSON, because a checkpoint does. Every reader
    rebuilding that dict by string key is every reader that has to be found
    again when a field is renamed — `Report` read five of them by hand.
    """
    if not isinstance(result, dict):
        return None
    found = result.get("diagnosis")
    if not isinstance(found, dict):
        return None
    try:
        return Diagnosis(**found)
    except TypeError:
        # A checkpoint written before this shape changed. The report says
        # nothing was diagnosed, which is true of what it can still read.
        log.warning("a stored diagnosis no longer fits its shape: %s", sorted(found))
        return None


def _judged(answer: Any, index: dict, not_checked: tuple, deps: Any) -> Any:
    """The gates a diagnosis has to pass before it is reported — one copy,
    `void_reason`, shared with the spine's `backend.diagnose.check`."""
    void = void_reason(answer, index)
    if void is not None:
        log.warning("task %s: diagnosis void — %s", deps.task.id, void)
        return envelope("empty", void, not_checked=list(not_checked))
    return envelope(
        "ok",
        "",
        diagnosis=asdict(answer),
        # The other half of "pointers, not quotes": the model named
        # lines, and this is what they said. The report renders these,
        # never the model's own rendering of them.
        quotes=quoted(answer, index),
        not_checked=list(not_checked),
    )


async def _reading(
    state: DAGState, deps: Deps, make_harness: Any, build_tools: Any
) -> Any:
    """v3.3: the model fetches its own evidence.

    The same answer shape and the same gates — what changes is where the
    lines it points at came from. `Evidence` is what makes that possible:
    every line any tool showed is numbered continuously, so `refs` are
    checked against what this run was actually shown rather than against a
    dossier built in advance.
    """
    from friday.sdk.evidence import Evidence
    from friday.sdk.toolset import RunContext
    from plugins.backend.agents.diagnose_prompt import build_reads_input
    from plugins.backend.graph.intake import intake_of
    from plugins.backend.graph.logs import _reported_at

    ctx = intake_of(state["intake"])
    placement = ctx.domain
    evidence = Evidence()
    # `mcp` is filled by the core per toolset (`caps.build_tools`).
    tools = (
        []
        if build_tools is None
        else build_tools(
            RunContext(
                task_id=deps.task.id,
                domain=placement,
                evidence=evidence,
                mcp={},
                reported_at=_reported_at(deps.task),
            )
        )
    )
    harness = make_harness(tools=tools)
    if harness is None:
        return envelope(
            "skipped", "no diagnose agent is configured, so nothing was diagnosed"
        )

    answer = await harness.run_structured(
        build_reads_input(
            report=ctx.request_text,
            placement=placement,
            not_checked=(),
        ),
        task_id=deps.task.id,
        node="diagnose",
    )
    # **The honest half is the tools\' own**, not the model\'s. What a read
    # left out is a fact about the read, and a model asked to remember it
    # reproduces it unreliably.
    not_checked = tuple(evidence.not_checked)
    if isinstance(answer, (Ask, HandOver)):
        # The model ended the loop with an Action instead of a diagnosis:
        # `hand_over(reason)` to the operator, or `ask_reporter(question)` for a
        # missing piece only the reporter has. Either may happen without having
        # read anything (a case it cannot start on), so this is checked before
        # the "answered without reading" gate below. The node returns the
        # Action; the run ends here rather than reaching `Report`.
        return answer
    if answer is None:
        return envelope(
            "error",
            harness.last_error or "the diagnose agent returned no answer",
            not_checked=list(not_checked),
        )
    if not evidence.index:
        # It answered without reading anything. A cause from an endpoint's
        # name alone reads exactly like one built from evidence, which is
        # the whole reason the gates exist.
        return envelope(
            "empty",
            "the diagnosis was written without reading a single line, so "
            "there is no evidence behind it",
            not_checked=list(not_checked),
        )
    return _judged(answer, evidence.index, not_checked, deps)


def diagnose_node(
    *,
    agent: str | None = None,
    #: Build a harness per run, with this run's tools — the tools carry this
    #: run's placement and numbering, so a harness built once at boot would
    #: read the previous case's service. `None` when no `diagnose` agent is
    #: configured — a fresh install, and every test that does not set one up.
    make_harness: Any = None,
    #: This run's tools from a `RunContext` — the graph builder's
    #: `caps.build_tools` over `backend.logs` and `backend.code`. `None`
    #: builds none (a test that reads nothing).
    build_tools: Any = None,
) -> Node:
    """Build node 4.

    The node skips, with a reason, when no agent is configured, and the
    graph still reaches `Report`. That is the difference between this and
    the graph the operator deleted: a node that skips says so where somebody
    reads it.
    """

    async def _diagnose(state: DAGState, deps: Deps) -> Any:
        if make_harness is None:
            return envelope(
                "skipped",
                "no diagnose agent is configured, so nothing was diagnosed",
            )
        return await _reading(state, deps, make_harness, build_tools)

    return Node("diagnose", _diagnose, agent=agent)
