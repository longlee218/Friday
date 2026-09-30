"""`memory_propose`: suggest something worth remembering, without writing it
yet — waits for the operator's mark.

Declared explicitly, not from a docstring: `_description` and `text`'s
`Field` render the model-facing text from `friday.kernel.toolsets.memory.
shared.TEXT_CHARS`.
"""

from __future__ import annotations

import logging
from typing import Annotated

from pydantic import Field

from friday.kernel.domain.memory import CandidateStatus, ModelMemoryKind
from friday.kernel.domain.state import FridayState
from friday.kernel.harness.harness import ToolContext, tool
from friday.kernel.memory import write
from friday.kernel.toolsets.memory.shared import TEXT_CHARS, bounded, state_of

__all__ = ["build_propose"]

log = logging.getLogger(__name__)


def _description() -> str:
    return (
        "Suggest something worth remembering, without writing it yet. Use "
        "this instead of memory_add when you believe something but are not "
        "fully confident of it, or when being wrong about it would cost more "
        "than an awkward sentence — a claim a later task might act on. The "
        "operator reviews it before anything reads it back; nothing changes "
        "for this run or any other in the meantime, and there is nothing to "
        "poll or wait for. Returns the candidate's id, for your own record."
    )


def build_propose(db):
    """`memory_propose`, bound to `db`."""

    @tool(description=_description())
    async def memory_propose(
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
        proposed = await write.propose(db, state, kept, kind=ModelMemoryKind.VOICE)
        log.info("memory proposed by %s: %r (%s)", state.agent, kept, proposed.status)
        if proposed.status != CandidateStatus.PENDING:
            # `propose_memory` resolves immediately when the message it is
            # scoped to already carries a verdict — the operator marked the
            # classification before this run got here. Reporting the actual
            # outcome is more useful than telling the model to wait for a
            # mark that has already happened.
            outcome = (
                "accepted"
                if proposed.status == CandidateStatus.ACCEPTED
                else "rejected"
            )
            return f"proposed as {proposed.id} — already marked {outcome}"
        return f"proposed as {proposed.id} — waiting for a mark"

    return memory_propose
