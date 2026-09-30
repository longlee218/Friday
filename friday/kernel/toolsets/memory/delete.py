"""`memory_delete`: remove something that turned out to be wrong.

Declared explicitly, not from a docstring: `_description` and `memory_id`'s
`Field` carry the model-facing text.
"""

from __future__ import annotations

import logging
from typing import Annotated

from pydantic import Field

from friday.kernel.domain.state import FridayState
from friday.kernel.harness.harness import ToolContext, tool
from friday.kernel.toolsets.memory.shared import no_such, state_of

__all__ = ["build_delete"]

log = logging.getLogger(__name__)


def _description() -> str:
    return (
        "Remove something that turned out to be wrong. For a line that is "
        "*false*, not one that is old — a fact you have just disproved, a "
        "preference the person has told you they no longer have. Something "
        "merely inaccurate is a memory_update. Deleting is visible to the "
        "operator afterwards, along with what the line said. Delete what is "
        "wrong; you do not have to tidy."
    )


def build_delete(db):
    """`memory_delete`, bound to `db`."""

    @tool(description=_description())
    async def memory_delete(
        ctx: ToolContext[FridayState],
        memory_id: Annotated[
            str, Field(description="The id exactly as memory_search returned it.")
        ],
    ) -> str:
        state = state_of(ctx)
        if not await db.memory_delete(state, memory_id):
            return no_such(memory_id)
        log.info("memory %s deleted by %s", memory_id, state.agent)
        return f"{memory_id} forgotten"

    return memory_delete
