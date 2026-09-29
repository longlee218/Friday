"""Node 4: say what caused it, and say what was not checked.

**The reads loop is the only diagnose mode** (ticket 05): the model fetches
its own evidence through the `backend.logs` and `backend.code` toolsets rather than
being handed a fixed dossier. The two fixed pre-fetch nodes that used to
build one — `FindRequestLog`, `ReadFailingCode` — are gone; `_reading` below
is what is left.

**The grounding gate** is the one guard here. `Diagnosis.refs` are **line
ids**, not quotes — the spec retired quote-checking on a measurement (ticket
16): the configured model quotes a JSON log line correctly 32 times in 40 and
points at one by id 20 times in 20. So the model names lines and code puts
the text back, and a pointer that does not resolve is refused like any wrong
ref. An answer built on something the model supplied itself is the failure
mode this whole board exists to avoid.

The spec has the *answer tool* refuse it, so the model can correct inside its
own turn budget (D8). The slice checks after the answer instead, and an
ungrounded answer is an envelope the graph hands over on rather than a reply.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from friday.sdk.toolset import tool
from friday.sdk.workflow import Ask, DAGState, Deps, HandOver, Node, envelope

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

#: Five rungs, named rather than numeric. A model asked for 0.0–1.0 answers
#: 0.85 whatever it means; a model asked to pick a rung picks one that can be
#: counted against the operator's own mark (ticket 14).
CONFIDENCE = ("certain", "likely", "possible", "unlikely", "guess")


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """What `Diagnose` answers (spec, D8).

    `not_checked` is **not** a field here, and that is a deliberate departure
    from the spec's shape. What was not checked is known by code — the log
    node knows its window was widened and found nothing, the code node knows
    it read HEAD rather than the running tag — and asking a model to remember
    those is asking it to reproduce, unreliably, a list this process already
    holds. The node merges the code-authored list into its envelope. What the
    model may still add is `next_checks`: what it would do next.
    """

    cause: str = field(
        metadata={
            "doc": "What caused the failure, in one or two sentences, in "
            "Vietnamese. Say whose fault it is — this service, a service it "
            "calls, or the request itself."
        }
    )
    confidence: Literal[CONFIDENCE] = field(  # type: ignore[valid-type]
        metadata={"doc": "How sure you are, as one of the five rungs."}
    )
    conclusive: bool = field(
        metadata={
            "doc": "true only if the evidence shown here would convince "
            "somebody who did not trust you. false costs nothing."
        }
    )
    refs: list[str] = field(
        default_factory=list,
        metadata={
            "doc": "The ids of the lines you are pointing at, off the left "
            "margin of what you were shown — `L12`, not the line's text. An "
            "id that names no line voids the answer."
        },
    )
    next_checks: list[str] = field(
        default_factory=list,
        metadata={
            "doc": "What you would look at next, if this is not conclusive. "
            "Naming the reporter's response is allowed."
        },
    )
    #: **At least one when `conclusive`**, enforced below (spec: "the answer
    #: shape forces it", tier 1 of self-questioning without a second model
    #: call). The mechanism is the point: *filling this in requires having
    #: thought of an alternative*, and a cause that survived one considered
    #: rival is worth more than one that was simply the first thing the
    #: evidence suggested. The cheapest of the three tiers, and the only one
    #: that costs no extra call.
    alternatives_rejected: list[dict] = field(
        default_factory=list,
        metadata={
            "doc": "The other explanations you considered and ruled out. "
            "Each is an object with `hypothesis` (what else it could have "
            "been), `why` (what rules it out) and `ref` (the line id that "
            "shows it, `L12`). At least one is required when `conclusive` "
            "is true. An id that names no line voids the answer."
        },
    )


def _rejected(answer: Diagnosis) -> list[dict]:
    """The alternatives that are actually filled in.

    A model answering the shape can hand back `[{}]` or entries missing the
    half that does the work; an empty hypothesis rules nothing out, and a
    reason with no pointer is an assertion. Counting only the complete ones
    is what keeps the gate from being satisfied by its own field existing.
    """
    kept = []
    for entry in answer.alternatives_rejected or ():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("hypothesis", "")).strip() and str(
            entry.get("why", "")
        ).strip():
            kept.append(entry)
    return kept


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


#: `L12`, `l12`, `L12:`, `line 12` — the same pointer, written by a model
#: that was told one format and reached for another. The digits are the
#: pointer; everything around them is decoration.
_REF = re.compile(r"(\d+)")


def _key(ref: str) -> str:
    found = _REF.search(ref or "")
    return f"L{int(found.group(1))}" if found else ""


def unresolved_refs(diagnosis: Diagnosis, index: dict[str, str]) -> list[str]:
    """Which of the answer's pointers name no line it was shown.

    An exact lookup, which is the point of pointers: a quote check has to
    decide how much rewrapping to forgive, and every threshold for that is a
    number nobody can defend.
    """
    pointed = [*diagnosis.refs]
    # **An alternative's pointer is a pointer.** It is shown to the operator
    # as evidence that something was ruled out, so an id naming no line
    # there is the same invention the main refs are checked for — and a gate
    # that checked only half the answer is a gate a model learns the shape
    # of.
    for entry in diagnosis.alternatives_rejected or ():
        if isinstance(entry, dict) and str(entry.get("ref", "")).strip():
            pointed.append(str(entry["ref"]))
    return [ref for ref in pointed if ref.strip() and _key(ref) not in index]


def quoted(diagnosis: Diagnosis, index: dict[str, str]) -> list[str]:
    """The lines the answer pointed at, in this process's own words for them
    — the half of "pointers, not quotes" that puts the text back."""
    return [index[_key(r)] for r in diagnosis.refs if _key(r) in index]



def _judged(answer: Any, index: dict, not_checked: tuple, deps: Any) -> Any:
    """The gates a diagnosis has to pass before it is reported.

    **One copy on purpose.** They are the difference between a diagnosis and
    a plausible sentence — and, while the dossier path this used to also
    serve was still around, the thing that kept a gate from holding on one
    path and not the other.
    """
    invented = unresolved_refs(answer, index)
    if invented:
        log.warning(
            "task %s: diagnosis points at lines it was not shown: %s",
            deps.task.id, invented,
        )
        return envelope(
            "empty",
            "the diagnosis pointed at "
            + ", ".join(repr(ref) for ref in invented[:3])
            + ", which names no line it was shown — so it was not built "
            "on the evidence and is not being reported",
            not_checked=list(not_checked),
        )
    if answer.conclusive and not _rejected(answer):
        # **The shape forcing the question** (spec, tier 1). A cause
        # nothing was weighed against is the first thing the evidence
        # suggested, which is exactly the answer a reader cannot tell
        # from a considered one. `conclusive` is the claim that the
        # alternatives were thought about, so it is the claim that has
        # to show one.
        return envelope(
            "empty",
            "the diagnosis called itself conclusive without naming one "
            "other explanation it ruled out, so nothing shows it was "
            "weighed against anything",
            not_checked=list(not_checked),
        )
    if answer.conclusive and not answer.refs:
        # Otherwise the gate is optional: an answer with no pointers has
        # nothing to refuse, and "conclusive" is exactly the claim that
        # needs one.
        return envelope(
            "empty",
            "the diagnosis called itself conclusive and pointed at "
            "nothing, so there is no evidence behind it to report",
            not_checked=list(not_checked),
        )

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
    from friday.sdk.toolset import RunContext
    from plugins.backend.graph.intake import intake_of
    from plugins.backend.graph.logs import _reported_at
    from plugins.backend.graph.prompt import build_reads_input
    from plugins.backend.toolsets.evidence import Evidence

    ctx = intake_of(state["intake"])
    placement = ctx.domain
    evidence = Evidence()
    # `mcp` is filled by the core per toolset (`caps.build_tools`).
    tools = [] if build_tools is None else build_tools(RunContext(
        task_id=deps.task.id, domain=placement, evidence=evidence, mcp={},
        reported_at=_reported_at(deps.task),
    ))
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
    *, agent: str | None = None,
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

    return Node(
        "diagnose", _diagnose, agent=agent
    )
