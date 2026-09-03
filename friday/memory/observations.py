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

__all__ = ["Category"]

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
