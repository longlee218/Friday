"""`backend.diagnose`: the agent `backend.trace_problem` grants — its answer
shape (`Diagnosis`) and its grounding gate (`check`).

Runs on the spine since build-the-spine ticket 14; the DAG node
(`graph/diagnose.py`) imports `Diagnosis` and the gate from here until ticket
16 deletes it. `max_turns` is ticket 06's number for `backend.explain` — a
starting value, not measured.

**The grounding gate** is the one guard here. `Diagnosis.refs` are **line
ids**, not quotes — the spec retired quote-checking on a measurement (ticket
16): the configured model quotes a JSON log line correctly 32 times in 40 and
points at one by id 20 times in 20. So the model names lines and code puts
the text back, and a pointer that does not resolve voids the answer, which
`run_agent` then hands over rather than drafting from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

from friday.sdk import AgentSpec, Budget
from plugins.backend.agents.diagnose_prompt import build_instructions

__all__ = [
    "CONFIDENCE",
    "DIAGNOSE",
    "Diagnosis",
    "check",
    "quoted",
    "unresolved_refs",
    "void_reason",
]

#: Five rungs, named rather than numeric. A model asked for 0.0–1.0 answers
#: 0.85 whatever it means; a model asked to pick a rung picks one that can be
#: counted against the operator's own mark (ticket 14).
#:
#: The `Literal` is the source and `CONFIDENCE` is read off it with
#: `get_args`, not the other way around: a `Literal` built from a variable
#: (`Literal[CONFIDENCE]`) is not a type a checker can see the members of,
#: whatever it does at runtime — this way there is still one place the five
#: rungs are spelled, and it is the one a checker can verify against.
_Confidence = Literal["certain", "likely", "possible", "unlikely", "guess"]
CONFIDENCE = get_args(_Confidence)


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
    confidence: _Confidence = field(
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
    alternatives_rejected: list[dict[str, Any]] = field(
        default_factory=list,
        metadata={
            "doc": "The other explanations you considered and ruled out. "
            "Each is an object with `hypothesis` (what else it could have "
            "been), `why` (what rules it out) and `ref` (the line id that "
            "shows it, `L12`). At least one is required when `conclusive` "
            "is true. An id that names no line voids the answer."
        },
    )


def _rejected(answer: Diagnosis) -> list[dict[str, Any]]:
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
        if (
            str(entry.get("hypothesis", "")).strip()
            and str(entry.get("why", "")).strip()
        ):
            kept.append(entry)
    return kept


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


def void_reason(answer: Diagnosis, index: dict[str, str]) -> str | None:
    """Why this answer is void against the lines the run was shown, or
    `None`. One copy: the DAG node and the spine both ask it."""
    invented = unresolved_refs(answer, index)
    if invented:
        return (
            "the diagnosis pointed at "
            + ", ".join(repr(ref) for ref in invented[:3])
            + ", which names no line it was shown — so it was not built "
            "on the evidence and is not being reported"
        )
    if answer.conclusive and not _rejected(answer):
        # **The shape forcing the question** (spec, tier 1). A cause nothing
        # was weighed against is the first thing the evidence suggested, which
        # is exactly the answer a reader cannot tell from a considered one.
        return (
            "the diagnosis called itself conclusive without naming one "
            "other explanation it ruled out, so nothing shows it was "
            "weighed against anything"
        )
    if answer.conclusive and not answer.refs:
        # Otherwise the gate is optional: an answer with no pointers has
        # nothing to refuse, and "conclusive" is exactly the claim that
        # needs one.
        return (
            "the diagnosis called itself conclusive and pointed at "
            "nothing, so there is no evidence behind it to report"
        )
    return None


def check(answer: Any, evidence: Any) -> str | None:
    """`AgentSpec.check` for `backend.diagnose`: an answer written without
    reading a line is void, whatever it says — a cause from an endpoint's
    name alone reads exactly like one built from evidence."""
    if evidence is None or not evidence.index:
        return (
            "the diagnosis was written without reading a single line, so "
            "there is no evidence behind it"
        )
    return void_reason(answer, evidence.index)


DIAGNOSE = AgentSpec(
    name="backend.diagnose",
    description="Finds what caused a failing request: reads the service's logs "
    "and its code at the running tag, and names the line that rejects it.",
    instructions=build_instructions(reads=True),
    result=Diagnosis,
    tier="flash",
    toolsets=("backend.logs", "core.repos", "core.memory", "core.skills"),
    budget=Budget(max_turns=20, tokens=500_000),
    temperature=0.0,
    check=check,
)
