"""What a decision about a task comes to.

Every graph in `friday/dag/` ends in one of these, and the pool in
`friday/tasks/` is what acts on it. They are vocabulary, not mechanism, which
is why they live here rather than in either: a graph node importing them from
the pool, or the pool importing them from the graph engine, was an import
cycle with no reason for the direction it happened to take.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["Action", "Ask", "HandOver", "Reply"]


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
class HandOver:
    """Nothing can be done automatically. A human picks it up.

    `reason` is quoted to the operator, never sent to a reporter under the
    operator's name — a node's own finding ("the cause mentions a
    migration"), or code's own ("no workflow for this yet"). Named for what
    it does (ticket 06): a node's agent can call `hand_over(reason)` itself
    to report this the same way it reports an answer, and a graph that
    reaches its end without deciding anything hands over by code, with no
    model asked. `Park` was the name before there was a tool by that name to
    confuse it with.

    `interruption` is set only for one specific shape of hand-over (ticket
    07): a node's agent called a tool the SDK stopped to ask about — applying
    a fix, so far — rather than one that decided nothing could be done. It is
    the SDK's own run state, serialized, so approving resumes the exact call
    that stopped rather than restarting the investigation to reach it again.
    `None` for every ordinary hand-over, which is most of them.
    """

    reason: str
    interruption: dict[str, Any] | None = None


Action = Ask | Reply | HandOver
