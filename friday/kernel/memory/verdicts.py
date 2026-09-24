"""The operator saying a classification was right, or wrong.

The agent may interrupt them for two things: it needs help, and it wants a
reply approved. Judging a classification is neither. A notification per
classification is one they would answer fifty times a day and then stop
reading — at which point the signal is dead and the examples keep growing
from things nobody looked at.

So the signal is one they give when they feel like it. A reaction is already
how people say things on Discord, it arrives over a connection that is
already open, and it costs nothing when unused.

**Silence is not approval.** This is the fourth place in this system where an
agent would otherwise learn from its own unreviewed output — after the voice
it writes in, the observations it records, and the facts it might extract.
Only a classification marked *right* becomes an example. One that was never
marked is one nobody read.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["EMOJI", "Mark", "mark_for"]


class Mark(StrEnum):
    """What the operator said. A closed set — there is no "maybe"."""

    RIGHT = "right"
    WRONG = "wrong"


#: Which reaction means what. A handful of obvious ones rather than a
#: configurable set: the operator has to remember these without looking them
#: up, and anything they have to look up they will not use.
#:
#: Keys are stored without the variation selector — see `_plain`.
EMOJI: dict[str, Mark] = {
    "✅": Mark.RIGHT,
    "☑": Mark.RIGHT,
    "❌": Mark.WRONG,
    "✖": Mark.WRONG,
}

#: 👍 and 👎 were here and are deliberately not. A thumbs-up is the most
#: ordinary reaction on Discord — "ok anh nhé" to a colleague — and every one
#: of them landing on a classified message would quietly become a training
#: example. That is exactly what this ticket's guarantee exists to prevent, so
#: the marks are ones nobody reaches for by habit.

#: U+FE0F, the emoji variation selector. Discord clients disagree about
#: whether to send it: the same ☑ arrives as U+2611 from one and
#: U+2611 U+FE0F from another. Comparing the raw string means the operator
#: reacts, nothing happens, and there is no error anywhere to notice.
_VARIATION_SELECTOR = "️"


def _plain(emoji: str) -> str:
    return emoji.strip().replace(_VARIATION_SELECTOR, "")


def mark_for(emoji: str) -> Mark | None:
    """The mark this reaction means, or None if it means nothing to us.

    Unrecognised reactions are ignored in silence. People react to messages
    for their own reasons, and a system that answered every one of them would
    be reading intent into a shrug.
    """
    return EMOJI.get(_plain(emoji))
