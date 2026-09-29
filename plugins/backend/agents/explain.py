"""`backend.explain`: the agent `backend.answer_question` grants.

Declared here so the action's contract names a registered agent (boot refusal
4); nothing runs it until build-the-spine ticket 15, which owns its
instructions and the checks on `Explanation`. The shape is board
`domains-plug-in` ticket 06 §7. Ticket 06 asks for the strong tier "as
diagnose"; `config.yaml` declares only `flash`, which is diagnose's, so it is
`flash`. Tokens are diagnose's; both numbers are starting values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from friday.sdk import AgentSpec, Budget

__all__ = ["EXPLAIN", "Explanation"]


@dataclass(frozen=True, slots=True)
class Explanation:
    """What `backend.explain` answers. Which repos were searched and which ref
    was read are filled by code, not asked of the model."""

    verdict: Literal["yes", "no", "partly", "unknown"] = field(
        metadata={"doc": "Does the code running now do what they asked about."}
    )
    answer: str = field(metadata={"doc": "One to three sentences, in Vietnamese."})
    conclusive: bool = field(
        metadata={"doc": "true only if the lines cited would convince a sceptic."}
    )
    refs: list[str] = field(
        default_factory=list,
        metadata={
            "doc": "The ids of the code or doc lines you are pointing at — `L12`. "
            "An id that names no line voids the answer."
        },
    )
    next_checks: list[str] = field(
        default_factory=list,
        metadata={"doc": "What you would read next, if this is not conclusive."},
    )


EXPLAIN = AgentSpec(
    name="backend.explain",
    description="Answers whether the code or docs running now do something, and "
    "how, citing the lines it read.",
    instructions="You answer whether the code or docs running now do what the "
    "reporter asked about, and how. Search and read before you answer; cite the "
    'lines you read. "Not found" is not "not there": a conclusive no needs '
    "the place that would have done it, read.",
    result=Explanation,
    tier="flash",
    toolsets=("backend.code", "backend.docs"),
    budget=Budget(max_turns=20, tokens=500_000),
    temperature=0.0,
)
