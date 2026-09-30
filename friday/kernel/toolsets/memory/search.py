"""`memory_search`: find what this channel already knows, before assuming
nothing is.

Declared explicitly, not from a docstring (ticket 23's own rule): `_description`
renders the model-facing text from `RESULTS`, and `query`'s description sits
beside the parameter rather than in an `Args:` block.
"""

from __future__ import annotations

import logging
from typing import Annotated

from pydantic import Field

from friday.kernel.domain.memory import ModelMemoryKind
from friday.kernel.domain.state import FridayState
from friday.kernel.harness.harness import ToolContext, tool
from friday.kernel.harness.instruction_prompt import memory_lines
from friday.kernel.toolsets.memory.shared import state_of

__all__ = ["RESULTS", "build_search"]

log = logging.getLogger(__name__)

#: What one search returns. Enough to choose from, few enough that the agent
#: still has to have written something worth finding.
RESULTS = 8


def _description() -> str:
    return (
        "Find what is already known about something, before assuming nothing "
        "is. Search first. What you are about to work out may have been "
        "worked out already, by an earlier run that wrote it down for "
        f"exactly this moment. Returns up to {RESULTS} lines, newest first — "
        "not scored or ranked, only ordered by when each one was written, "
        f"so a precise memory older than {RESULTS} vaguer ones on the same "
        "words will not be in the list. Narrow the query rather than trust "
        "the order. Each line is `id: text` — the id is what memory_update "
        "and memory_delete take, so keep it if you intend to correct or "
        "remove that line. Nothing found comes back as a plain sentence "
        "saying so, which is an answer, not an error. Only this channel's "
        "memory is searched. There is no way to reach another room's, and "
        "nothing you write here will be visible there."
    )


def build_search(db):
    """`memory_search`, bound to `db`."""

    @tool(description=_description())
    async def memory_search(
        ctx: ToolContext[FridayState],
        query: Annotated[
            str,
            Field(
                description="A phrase describing what you want to know, in the words "
                "you would use to describe it — not an id, and not a question."
            ),
        ],
    ) -> str:
        found = await db.memory_search(
            state_of(ctx), query, kind=ModelMemoryKind.VOICE, limit=RESULTS
        )
        log.info("memory searched: %r -> %d", query, len(found))
        return memory_lines(found)

    return memory_search
