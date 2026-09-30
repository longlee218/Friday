"""`memory_add`: write down something a later run would otherwise have to
work out again, read back as fact.

Declared explicitly, not from a docstring: `_description` and `text`'s
`Field` render the model-facing text from `friday.kernel.toolsets.memory.
shared.TEXT_CHARS`.
"""

from __future__ import annotations

import logging
from typing import Annotated

from pydantic import Field

from friday.kernel.domain.memory import ModelMemoryKind
from friday.kernel.domain.memory_guard import InstructionShaped
from friday.kernel.domain.state import FridayState
from friday.kernel.harness.harness import ToolContext, tool
from friday.kernel.memory import write
from friday.kernel.toolsets.memory.shared import TEXT_CHARS, bounded, state_of

__all__ = ["build_add"]

log = logging.getLogger(__name__)


def _description() -> str:
    return (
        "Write down something a later run would otherwise have to work out "
        "again. Worth writing: how a system actually behaves once you have "
        "established it, a person's standing preference, a step that turned "
        "out to be necessary. Not worth writing outright: anything readable "
        "off the task you are working on. Something you believe but are not "
        "fully confident of is `memory_propose`'s job, not this one's — this "
        "is read back as fact, not as a guess, so write here only what you "
        "would stand behind in a month. Search before you write. A second "
        "copy of something already known is worse than nothing: it takes a "
        "slot, and the two will disagree the day one of them is corrected. "
        "Returns the new id, so you can correct it later in this same run."
    )


def build_add(db):
    """`memory_add`, bound to `db`."""

    @tool(description=_description())
    async def memory_add(
        ctx: ToolContext[FridayState],
        text: Annotated[
            str,
            Field(
                description=f"One sentence, at most {TEXT_CHARS} characters, that "
                "will make sense to a run that has none of your current context."
            ),
        ],
    ) -> str:
        state = state_of(ctx)
        kept = bounded(text)
        try:
            written = await write.add(db, state, kept, kind=ModelMemoryKind.VOICE)
        except InstructionShaped as refused:
            log.info("memory refused for %s: %s", state.agent, refused)
            return str(refused)
        if written is None:
            # The store's own cap, not a failure — see `Database.MEMORY_PER_CHANNEL`.
            # Nothing is evicted to make room, so the model has to make room
            # itself: correct something with memory_update, or remove
            # something wrong with memory_delete.
            return (
                "this channel's memory is full — use memory_update to correct "
                "something already here, or memory_delete to remove something "
                "that turned out to be wrong, then try again"
            )
        log.info("memory added by %s: %r", state.agent, kept)
        return f"remembered as {written.id}"

    return memory_add
