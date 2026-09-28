"""Transcript-compaction bookkeeping per task — a `Database` mixin."""

from __future__ import annotations

from friday.store._common import *  # noqa: F401,F403 (shared store internals)


class CompactionRepo:

    async def record_ineffective_compaction(self, task_id: int) -> int:
        """One more pass where truncating this task's transcript still left
        it over budget. Returns the new count.

        Upserted the same way `mark_extraction` is — one current count per
        task, created on the first ineffective pass. Never reset: a task
        whose single message is larger than the budget stays that size, so
        there is no future pass on which trying again would help.
        """
        async with self._sessions.begin() as session:
            existing = await session.get(schema.CompactionState, task_id)
            if existing is None:
                session.add(schema.CompactionState(task_id=task_id, ineffective_count=1))
                return 1
            existing.ineffective_count += 1
            return existing.ineffective_count

    async def compaction_on_cooldown(self, task_id: int) -> bool:
        """Whether node 0 should stop attempting budget-based truncation for
        this task — `COMPACTION_COOLDOWN_AFTER` ineffective passes reached.
        `False` for a task nothing has recorded against, which is every task
        before its first ineffective pass."""
        return await self.compaction_ineffective_count(task_id) >= self.COMPACTION_COOLDOWN_AFTER

    async def compaction_ineffective_count(self, task_id: int) -> int:
        """How many passes in a row truncation has failed to help — `0` for a
        task nothing has recorded against. The count `compaction_on_cooldown`
        thresholds; kept as its own read so the operator's own view of a
        stuck task (and this store's own tests) can say *how* stuck, not
        only whether."""
        async with self._sessions() as session:
            row = await session.get(schema.CompactionState, task_id)
            return row.ineffective_count if row is not None else 0
