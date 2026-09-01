"""What a step learned while it was working.

Written to a staging tier and read by nothing. That restraint is the whole
ticket: an agent given its own unreviewed notes as context drifts, and the drift
has no floor — it is the same failure as learning a voice from its own replies,
one layer up. Promotion is a separate pass over work a human approved.

The runtime attaches the task and the time rather than asking for them. A model
asked for a timestamp invents one, and a model asked which task it is working on
is sometimes wrong about it — neither is a thing to take on trust when the point
is a durable record.
"""

from __future__ import annotations

import logging
from enum import StrEnum

__all__ = ["Category", "remember_tool"]

log = logging.getLogger(__name__)


class Category(StrEnum):
    """Closed, because an open vocabulary is a vocabulary nothing can query."""

    #: How something works, learned from looking. "Checkout runs on cluster b."
    FACT = "fact"
    #: A person's preference or habit. "Minh always sends a curl."
    PERSON = "person"
    #: Something to do differently next time. "Ask for the env first, it halves
    #: the round trips."
    LESSON = "lesson"


def remember_tool(db, *, task_id: int):
    """Build the `remember` a step calls.

    Bound to one task, so the step cannot record against another by naming it —
    the parameters a model supplies are the parameters it can get wrong.
    """

    async def remember(category: str, text: str) -> str:
        """Note something learned while working on this task."""
        try:
            known = Category(category)
        except ValueError:
            # Returned, not raised. A raising tool ends the run, and the step
            # had more to do than this note.
            return (
                f"{category!r} is not a category I keep. "
                f"Use one of: {', '.join(c.value for c in Category)}."
            )
        await db.record_observation(task_id=task_id, category=known.value, text=text)
        log.info("task %d noted a %s: %r", task_id, known.value, text[:80])
        return "noted"

    return remember
