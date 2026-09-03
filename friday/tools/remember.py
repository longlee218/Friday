"""Noting something learned while working on a task.

Bound to one task, so a step cannot record against another by naming it — the
parameters a model supplies are the parameters it can get wrong.

**What it writes is staged, never promoted.** `remember` puts a row in
`observations`, which is a tier nothing reads back into a prompt;
`friday/memory/notes.py` moves only what an approved outcome corroborates.
That separation is the whole point: an agent that could write its own
long-term memory would be an agent that could talk itself into anything.

**Not an SDK tool yet, deliberately.** It returns a plain async function: no
`@tool`, so nothing can hand it to an agent, and nothing does. It lived in
`friday/memory/observations.py`, where it read like a working tool to anyone
who found it — a thing that looks like a tool and is not is worse than either.
It is here so the answer to "what tools does this system have?" includes it
with its state visible, rather than in a package where its absence from that
answer looked like the answer.

Decorating it is one line, and the line to add when an agent is actually given
it — with the tests that call it directly rewritten to drive it through a
Harness, the way the other tools are tested.
"""

from __future__ import annotations

import logging

from friday.memory.observations import Category

__all__ = ["remember_tool"]

log = logging.getLogger(__name__)


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
