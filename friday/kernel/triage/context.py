"""What triage is shown about one mention, gathered in one place.

Board `what-the-room-already-knows`, tickets 14/15, D26: each prompt family
that gathers anything has exactly one gather function, beside its prompt
module, returning one frozen value — content, never rendered prompt text
(D4). This is the smaller of the two families' builders and settles the
shape the full build in `friday/kernel/extraction/context.py` follows: where the
module lives, what it returns, what it logs, what it may not do.

**This module reads. It never writes, and it must never import a section
builder, construct a `Section`, or join anything** — D4's rule, held to more
strictly than `friday/kernel/triage/prompt.py` itself: a prompt module may import
section builders and call `assemble`; the module that gathers what feeds it
may not. `tests/test_context_builders.py` polices both halves with an `ast`
test, the same way `test_prompt_sections.py` already polices assembly.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from friday.kernel.domain.models import InboundEvent, Memory

__all__ = ["LightContext", "build_light_context"]

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LightContext:
    """Everything triage is shown about one mention — D3's light build has
    exactly two inputs, and this is that pair as one value rather than two
    arguments a caller could pass out of order or forget.

    `turn` is the messages of this person's turn, oldest first, exactly as
    `TriageRunner.turn_from` computed it — this value does not recompute a
    turn and never will (see `build_light_context`). `summary` is the room's
    active `summary` row, or `None` for a room nobody has summarised.

    It was `room`, a whole `ChannelContext` of which triage rendered one
    section. The file is gone (board `read-it-the-way-the-operator-does`,
    ticket 10), and the summary is the one kind `readers_for` gives triage,
    so it is the one thing this carries.
    """

    turn: Sequence[InboundEvent]
    summary: Memory | None


async def build_light_context(
    summaries, *, channel_id: str, turn: Sequence[InboundEvent]
) -> LightContext:
    """The one place triage resolves a room.

    `turn` arrives already computed. `TriageRunner.turn_from` decides what
    one person's turn is for its own "has this turn closed" question, not
    only for this prompt — a second computation here would be a second place
    deciding what a turn is, which is exactly the kind of drift D26 exists to
    close. Callers with no turn to give (a bare unit test, a probe) decide
    their own fallback before calling this; it is not this function's
    business.

    `summaries` is the store, or anything with `room_summary(channel_id)`;
    `None` for a caller with nothing to resolve from — a test with no store
    wired — and the result carries no summary, the same as a room nobody
    has summarised gets.
    """
    summary = (
        await summaries.room_summary(channel_id) if summaries is not None else None
    )
    log.debug(
        "light context for %s: %d message(s), %d chars, room summary %s",
        channel_id,
        len(turn),
        sum(len(e.text) for e in turn),
        "present" if summary is not None else "absent",
    )
    return LightContext(turn=turn, summary=summary)
