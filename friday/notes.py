"""What survives a task.

An observation is a guess made while working. A note is something the system
will act on for months. What separates them is that a human approved the work
the guess came from — a wrong guess made under pressure must not become a
permanent belief.

Some categories need more than approval. One observation of how a system works
can simply be wrong, and a habit seen once is not a habit; a lesson the operator
already approved is a judgement they made, not a reading that might be
misread.

Notes live in the database like everything else. The ticket described a file,
and the property it wanted from one is real — the block is **rewritten from the
promoted set, in a stable order**, never appended to. That is what lets it sit
in the early part of a prompt, where a byte that moves costs a cache hit on
every call after it. Where the bytes are stored is not what gives them that.
"""

from __future__ import annotations

import logging

from friday.db import Database
from friday.observations import Category

__all__ = ["CORROBORATION", "Promotion"]

log = logging.getLogger(__name__)

#: How many approved tasks have to agree before something is believed.
CORROBORATION: dict[str, int] = {
    # A reading of how something works, which one task can get wrong.
    Category.FACT: 2,
    # Seen once is not a habit.
    Category.PERSON: 2,
    # A judgement the operator already approved by approving the work.
    Category.LESSON: 1,
}

#: A prompt has room for a handful of these, not a filing cabinet.
KEEP = 40


class Promotion:
    """Turns staged observations into notes, and forgets the rest."""

    def __init__(self, *, db: Database, keep: int = KEEP) -> None:
        self._db = db
        self._keep = keep

    async def run_once(self) -> int:
        """Consider everything staged. Returns how many became notes.

        Considering uses an observation up, whether or not it was promoted.
        Leaving it staged means every later pass counts the same evidence again,
        and one observation corroborates itself into a belief.
        """
        staged = await self._db.observations(limit=1000)
        if not staged:
            return 0

        approved = await self._db.approved_task_ids()
        promoted = 0
        for (category, text), seen in _tally(staged, approved).items():
            support = await self._db.support_note(
                category=category, text=text, by=seen
            )
            if support >= CORROBORATION.get(category, 2):
                promoted += 1
        await self._db.clear_observations([o.id for o in staged])
        await self._db.trim_notes(keep=self._keep)
        if promoted:
            log.info("promoted %d observation(s) into long-term notes", promoted)
        return promoted

    async def believed(self) -> list:
        """The notes that have enough behind them to act on.

        A row below its threshold is evidence waiting for a second sighting,
        not something to put in a prompt.
        """
        return [
            note
            for note in await self._db.notes(limit=self._keep)
            if note.support >= CORROBORATION.get(note.category, 2)
        ]

    async def render(self) -> str:
        """The block that goes in a prompt, or nothing at all.

        Rebuilt from the promoted set in a stable order, so two renders between
        promotions are byte-identical.
        """
        notes = await self.believed()
        if not notes:
            return ""
        lines = [f"- {n.text}" for n in notes]
        return "What I have learned so far:\n" + "\n".join(lines)


def _tally(staged, approved: set[int]) -> dict[tuple[str, str], int]:
    """How many *approved* tasks said each thing.

    Counted per task, not per observation: a step that writes the same note
    twice within one task has not corroborated anything.
    """
    seen: dict[tuple[str, str], set[int]] = {}
    for observation in staged:
        if observation.task_id not in approved:
            continue
        seen.setdefault((observation.category, observation.text), set()).add(
            observation.task_id
        )
    return {key: len(tasks) for key, tasks in seen.items()}
