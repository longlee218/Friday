"""Node 4: say what caused it, and say what was not checked.

**No tools on this path** (ticket 00). The dossier and the source excerpts
are already in the prompt, and the Collector that would fetch more is ticket
15, gated on ticket 14's baseline. So this node is one model call over a
fixed set of evidence — which is also what makes it measurable: ticket 14
freezes that evidence and re-runs the call alone.

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
Ticket 05 owns moving it into the tool.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from friday.dag.api_issue.code import code_of, codes_of
from friday.dag.api_issue.logs import dossier_of, histogram_of
from friday.dag.api_issue.prompt import build_input, numbered
from friday.dag.engine import DAGDeps, DAGState, Node, envelope

__all__ = [
    "Diagnosis",
    "diagnose_node",
    "diagnosis_of",
    "unresolved_refs",
]

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
    return [ref for ref in diagnosis.refs if ref.strip() and _key(ref) not in index]


def quoted(diagnosis: Diagnosis, index: dict[str, str]) -> list[str]:
    """The lines the answer pointed at, in this process's own words for them
    — the half of "pointers, not quotes" that puts the text back."""
    return [index[_key(r)] for r in diagnosis.refs if _key(r) in index]



def diagnose_node(
    *, harness: Any = None, agent: str | None = None, timeout_seconds: float | None = None
) -> Node:
    """Build node 4.

    `harness` is `None` when no `diagnose` agent is configured — a fresh
    install, and every test that does not set one up. The node skips, with a
    reason, and the graph still reaches `Report`. That is the difference
    between this and the graph the operator deleted: a node that skips says
    so where somebody reads it.
    """

    async def _diagnose(state: DAGState, deps: DAGDeps) -> Any:
        if harness is None:
            return envelope(
                "skipped",
                "no diagnose agent is configured, so nothing was diagnosed",
            )

        dossier, log_not_checked = dossier_of(state["find_request_log"])
        histogram = histogram_of(state["find_request_log"])
        code, code_not_checked = code_of(state["read_failing_code"])
        codes = codes_of(state["read_failing_code"])
        not_checked = (*log_not_checked, *code_not_checked)

        if not dossier and not code and not codes:
            # Nothing to reason over. A model asked to diagnose an empty
            # dossier writes a plausible cause from the endpoint's name
            # alone, and it reads exactly like one built from evidence.
            return envelope(
                "empty",
                "neither a log line nor a line of source reached this node, "
                "so there was nothing to diagnose from",
                not_checked=list(not_checked),
            )

        params = state["prepare"]
        shown_dossier, shown_code, index = numbered(dossier, code)
        answer = await harness.run_structured(
            build_input(
                report=getattr(params, "summary", "") or "",
                dossier=shown_dossier,
                code=shown_code,
                not_checked=not_checked,
                histogram=histogram,
                codes=codes,
            ),
            task_id=deps.task.id,
            node="diagnose",
        )
        if answer is None:
            return envelope(
                "error",
                harness.last_error or "the diagnose agent returned no answer",
                not_checked=list(not_checked),
            )

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

    return Node(
        "diagnose", _diagnose, agent=agent, timeout_seconds=timeout_seconds
    )
