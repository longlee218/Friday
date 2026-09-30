"""`memory_update`: correct something already written down, in place.

Declared explicitly, not from a docstring: `_description` and each
parameter's `Field` carry the model-facing text.
"""

from __future__ import annotations

import logging
from typing import Annotated

from pydantic import Field

from friday.kernel.domain.memory_guard import InstructionShaped
from friday.kernel.domain.state import FridayState
from friday.kernel.harness.harness import ToolContext, tool
from friday.kernel.memory import write
from friday.kernel.toolsets.memory.shared import bounded, no_such, state_of

__all__ = ["build_update"]

log = logging.getLogger(__name__)


def _description() -> str:
    return (
        "Correct something already written down, in place. Use this rather "
        "than adding a second line when what is stored is now wrong or "
        "incomplete — two lines that contradict each other are worse than "
        "either alone, and nothing later can tell which one won. The "
        "previous text is replaced, not kept. Correct what is wrong; do not "
        "rewrite a line that is merely phrased differently from how you "
        "would phrase it."
    )


def build_update(db):
    """`memory_update`, bound to `db`."""

    @tool(description=_description())
    async def memory_update(
        ctx: ToolContext[FridayState],
        memory_id: Annotated[
            str, Field(description="The id exactly as memory_search returned it.")
        ],
        text: Annotated[
            str,
            Field(
                description="What it should say instead, in full — this replaces the "
                "line rather than being appended to it."
            ),
        ],
    ) -> str:
        state = state_of(ctx)
        try:
            updated = await write.update(db, state, memory_id, bounded(text))
        except InstructionShaped as refused:
            log.info("memory update refused for %s: %s", state.agent, refused)
            return str(refused)
        if updated is None:
            return no_such(memory_id)
        log.info("memory %s updated by %s", memory_id, state.agent)
        return f"{memory_id} updated"

    return memory_update
