"""What a decision about a task comes to.

Every path that decides what to do with a task — the deterministic one in
`friday/workflows/` and the graphs in `friday/dag/` — ends in one of these.
They are vocabulary, not mechanism, which is why they live here rather than in
either: a graph node importing them from the loop, or the loop importing them
from the graph engine, was an import cycle with no reason for the direction it
happened to take.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Action", "Ask", "Park", "Reply"]


@dataclass(frozen=True, slots=True)
class Ask:
    """Ask the reporter for something. The text is ready to send."""

    text: str


@dataclass(frozen=True, slots=True)
class Reply:
    """An answer. Unlike an `Ask`, this waits for approval.

    The asymmetry is the point: asking for a correlationId costs a question if
    it is wrong, and asserting a cause costs the operator's credibility with
    their own team.
    """

    text: str


@dataclass(frozen=True, slots=True)
class Park:
    """Nothing can be done automatically. A human picks it up."""

    reason: str


Action = Ask | Reply | Park
