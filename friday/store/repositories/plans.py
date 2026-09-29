"""The spine's repository — a `Database` mixin (build-the-spine tickets 12, 14).

Step results (ticket 12), every plan version (`plans`) and the pass's one
`deliver` transaction (ticket 14). The runner (`friday/kernel/spine/runner.py`)
is the only caller of `put_step_result` — a guard in `tests/test_runner.py`
holds that.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from friday.sdk.outbox import Kind
from friday.store._common import *


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
        pass_no: int = 1,
    ) -> None:
        """Store what a step came to in this pass. A key already stored for
        this pass is left as it was: a stored result is never rewritten, so a
        step re-run after a crash between its end and this write cannot
        replace what readers saw."""
        statement = insert(schema.StepResult).values(
            task_id=task_id,
            step_key=step_key,
            pass_no=pass_no,
            step_id=step_id,
            plan_version=plan_version,
            kind=kind,
            body=body,
            created_at=_now(),
        )
        async with self._sessions.begin() as session:
            await session.execute(statement.on_conflict_do_nothing())

    async def step_results(self, task_id: int) -> dict[str, schema.StepResult]:
        """Every stored result of this task, by `step_key` — the newest pass's
        row where a key has several."""
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.StepResult)
                .where(schema.StepResult.task_id == task_id)
                .order_by(schema.StepResult.pass_no)
            )
            return {row.step_key: row for row in rows}

    async def put_plan(
        self,
        *,
        task_id: int,
        version: int,
        replaces: str | None,
        cause: str,
        placement: list,
        body: dict | None,
        hash: str | None,
        gate_errors: list,
        pass_no: int,
    ) -> schema.PlanVersion:
        """Store one plan version and return what is stored under
        `(task_id, version)` — the first write wins, so a crash that re-ran
        the Planner reads back the version it stored before."""
        statement = insert(schema.PlanVersion).values(
            task_id=task_id,
            version=version,
            replaces=replaces,
            cause=cause,
            placement=placement,
            body=body,
            hash=hash,
            gate_errors=gate_errors,
            pass_no=pass_no,
            created_at=_now(),
        )
        async with self._sessions.begin() as session:
            await session.execute(statement.on_conflict_do_nothing())
            return await session.get(schema.PlanVersion, (task_id, version))

    async def plans(self, task_id: int) -> list[schema.PlanVersion]:
        """Every plan version of this task, oldest first."""
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.PlanVersion)
                .where(schema.PlanVersion.task_id == task_id)
                .order_by(schema.PlanVersion.version)
            )
            return list(rows)

    async def asks_since(self, task_id: int, since: datetime | None) -> int:
        """Questions queued to the reporter about this task after `since`
        (all of them for `None`) — what `MAX_ASKS_PER_TASK` counts."""
        query = select(func.count()).where(
            schema.Outbound.task_id == task_id,
            schema.Outbound.kind == str(Kind.ASK_FOR_DETAILS),
        )
        if since is not None:
            query = query.where(schema.Outbound.created_at > since)
        async with self._sessions() as session:
            return int(await session.scalar(query) or 0)

    async def deliver_pass(
        self,
        *,
        task_id: int,
        pass_no: int,
        state: TaskState,
        rows: Sequence[dict] = (),
        card: tuple[str, Callable[[int], str]] | None = None,
        pause: str | None = None,
        hold: bool = False,
        heard_until: datetime | None = None,
    ) -> str | None:
        """A pass's `deliver`, in one transaction: queue `rows` (keyword
        arguments of a queued row), an approval `card` — `(sender,
        text for the reply's id)` — for the first of them, the hand-over
        reason `pause`, and the task's move to `state`.

        `state` `pending` starts the next pass on the spot (a reporter message
        that landed mid-pass). Only the pass the task is on, while it is
        `pending`, delivers: a re-run after a crash between this commit and
        the workflow recording it finds the task moved on and queues nothing
        twice.

        `hold` (an `Ask`): a reporter turn newer than `heard_until` — the
        newest one this pass's Intake read — holds the question back and
        starts the next pass instead. Checked inside the transaction, so a
        reply that lands while the question is being queued cannot be lost.
        The state the task moved to, or `None` when it did not deliver."""
        async with self._sessions.begin() as session:
            task = await session.get(schema.Task, task_id)
            if (
                task is None
                or task.state != TaskState.PENDING
                or task.pass_no != pass_no
            ):
                return None
            if hold and await self._heard_since(session, task_id, heard_until):
                rows, card, pause, state = (), None, None, TaskState.PENDING
            if not may_move(task.state, state):
                raise IllegalTransition(f"task {task_id} cannot go pending -> {state}")
            queued = [self._outbound_row(task_id=task_id, **row) for row in rows]
            session.add_all(queued)
            if card is not None and queued:
                await session.flush()
                sender, render = card
                session.add(
                    self._outbound_row(
                        task_id=task_id,
                        conversation=rows[0]["conversation"],
                        kind=Kind.APPROVAL_CARD,
                        sender=sender,
                        text=render(queued[0].id),
                        approves=queued[0].id,
                    )
                )
            if pause is not None:
                await session.execute(self._pause_statement(task_id, pause))
            task.state = str(state)
            if state == TaskState.PENDING:
                task.pass_no = pass_no + 1
                task.pass_cause = "reply"
            return str(state)

    async def _heard_since(self, session, task_id: int, since: datetime | None) -> bool:
        """Whether the reporter said anything on this task after `since`
        (anything at all for `None`)."""
        opening = await self._reporter_turns(session, task_id)
        if opening is None:
            return False
        newest = await session.scalar(
            select(func.max(schema.Message.created_at)).where(*opening[3])
        )
        return newest is not None and (since is None or newest > since)
