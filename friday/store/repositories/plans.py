"""The plans repository — a `Database` mixin (build-the-spine ticket 12).

Step results now; the `plans` table (every version) joins it in ticket 14.
The runner (`friday/kernel/spine/runner.py`) is the only caller of
`put_step_result` — a guard in `tests/test_runner.py` holds that.
"""

from __future__ import annotations

from friday.store._common import *  # noqa: F401,F403 (shared store internals)


class PlansRepo:

    async def put_step_result(
        self,
        *,
        task_id: int,
        step_key: str,
        step_id: str,
        plan_version: int,
        kind: str,
        body: dict,
    ) -> None:
        """Store what a step came to. A key already stored is left as it was:
        a stored result is never rewritten, so a step re-run after a crash
        between its end and this write cannot replace what readers saw."""
        statement = insert(schema.StepResult).values(
            task_id=task_id, step_key=step_key, step_id=step_id,
            plan_version=plan_version, kind=kind, body=body, created_at=_now(),
        )
        async with self._sessions.begin() as session:
            await session.execute(statement.on_conflict_do_nothing())

    async def step_results(self, task_id: int) -> dict[str, schema.StepResult]:
        """Every stored result of this task, by `step_key`."""
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.StepResult).where(schema.StepResult.task_id == task_id)
            )
            return {row.step_key: row for row in rows}
