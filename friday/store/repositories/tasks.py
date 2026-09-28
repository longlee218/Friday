"""The tasks repository — a `Database` mixin (ticket 16): a task's lifecycle.
Just over 200 lines until the extractor's rows (`extraction_mark`,
`mark_extraction`, `set_task_params`) and the pause (`set_pause`,
`pauses_for`) go in build-the-spine tickets 12 and 16."""

from __future__ import annotations

from friday.store._common import *  # noqa: F401,F403 (shared store internals)


class TasksRepo:

    # ---- tasks ---------------------------------------------------------

    async def create_task(
        self,
        *,
        conversation: ConversationId,
        type: str,
        state: str,
        confidence: float,
        params: dict,
    ) -> Task:
        row = schema.Task(
            conversation_id=str(conversation),
            type=type,
            state=state,
            confidence=confidence,
            params=params,
            created_at=_now(),
        )
        async with self._sessions.begin() as session:
            session.add(row)
        return _task(row)

    async def open_task_for(self, conversation: ConversationId) -> Task | None:
        """The task this conversation is already working on, if any.

        Open means anything not finished: a follow-up belongs to work in flight,
        whatever stage it has reached.
        """
        async with self._sessions() as session:
            row = await session.scalar(
                select(schema.Task)
                .where(
                    schema.Task.conversation_id == str(conversation),
                    schema.Task.state.in_([str(s) for s in OPEN]),
                )
                .order_by(schema.Task.id.desc())
                .limit(1)
            )
            return _task(row) if row else None

    async def move_task(self, task_id: int, state: TaskState) -> None:
        """Move a task, refusing anything the graph does not permit.

        Enforced here rather than at each caller: this is the one place every
        move passes through, and a state written by a caller that skipped the
        check is a task nobody polls again.
        """
        async with self._sessions.begin() as session:
            current = await session.scalar(
                select(schema.Task.state).where(schema.Task.id == task_id)
            )
            if current is None:
                raise IllegalTransition(f"no task {task_id}")
            if not may_move(current, state):
                raise IllegalTransition(
                    f"task {task_id} cannot go {current} -> {state}"
                )
            await session.execute(
                update(schema.Task)
                .where(schema.Task.id == task_id)
                .values(state=str(state))
            )

    async def extraction_mark(self, task_id: int) -> ExtractionMark | None:
        """What node 0 last extracted for this task, and from what.

        `None` means nothing has been extracted for it yet: the first pass, or
        a pass whose extraction produced nothing worth remembering.

        **Nothing deletes a mark, and that is deliberate rather than missing.**
        A mark is keyed on a task and task ids are never reused, so a mark for
        a finished task is dead weight bounded by the number of tasks — not the
        "an approved patch outlived the task it belonged to" failure this repo
        has already shipped once, because a mark cannot be acted on: its only
        reader asks for one task by id, and a reopened task's mark is still
        true, since the same text still yields the same answer. If these ever
        need pruning it is the same job as `KEEP_MODEL_CALLS_DAYS`, not a
        cascade.
        """
        async with self._sessions() as session:
            row = await session.get(schema.ExtractionMark, task_id)
            if row is None:
                return None
            return ExtractionMark(
                fingerprint=row.fingerprint,
                params=dict(row.params or {}),
                asked_about=tuple(row.clarify_fields or ()),
                because=row.clarify_because,
            )

    async def mark_extraction(self, task_id: int, mark: ExtractionMark) -> None:
        """Record what the extraction was made from and what it came to.

        Upserted, because there is one current answer per task: a history of
        superseded fingerprints would be a log with no reader.

        The mark carries its own copy of what the extractor produced, which is
        what makes it safe for `set_task_params` to be a separate write: a
        crash between the two leaves a mark whose replay fills the same values
        again, rather than a mark pointing at values nobody stored. An earlier
        version of this docstring claimed the two were one call. They are not.
        """
        values = dict(
            fingerprint=mark.fingerprint,
            params=mark.params,
            clarify_fields=list(mark.asked_about),
            clarify_because=mark.because,
        )
        async with self._sessions.begin() as session:
            existing = await session.get(schema.ExtractionMark, task_id)
            if existing is None:
                session.add(schema.ExtractionMark(task_id=task_id, **values))
                return
            for field_name, value in values.items():
                setattr(existing, field_name, value)

    async def set_task_params(self, task_id: int, params: dict) -> None:
        await self._set_task(task_id, params=params)

    async def _set_task(self, task_id: int, **values) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(schema.Task).where(schema.Task.id == task_id).values(**values)
            )

    async def announced(self, kind: str, *, state: str, limit: int = 20) -> dict:
        """What has already been said about each task in this state.

        Task id -> the set of texts already queued for it. The outbox row is
        the record of having said something, so asking it directly beats
        denormalising the same fact onto the task and keeping the two in step.

        The *text*, not merely the fact of a row. "Told once, ever" is only
        right while the message is the same message: a graph pauses with its
        own question, and when the reporter answers it can pause on a
        different one. A caller that asked only "was anything said?" would see
        the first row and swallow the second question.
        """
        async with self._sessions() as session:
            rows = await session.execute(
                select(schema.Outbound.task_id, schema.Outbound.text)
                .join(schema.Task, schema.Task.id == schema.Outbound.task_id)
                .where(
                    schema.Task.state == str(state),
                    schema.Outbound.kind == str(kind),
                )
                .limit(limit * 8)
            )
            said: dict[int, set[str]] = {}
            for task_id, text in rows:
                said.setdefault(task_id, set()).add(text)
            return said

    async def set_pause(self, task_id: int, question: str) -> None:
        """Record the specific reason a task handed a person the work, so
        `_raise_hands` carries it rather than the task's bare type (ticket 06 —
        the workflow itself suspends now, so this is the pool's note beside it,
        not a checkpoint). Scrubbed: a reason can be a node's exception text."""
        statement = insert(schema.DagState).values(
            task_id=task_id,
            dag_name="",
            results={},
            paused_at_node="",
            paused_question=scrub(question),
            updated_at=_now(),
        )
        async with self._sessions.begin() as session:
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[schema.DagState.task_id],
                    set_={
                        "paused_question": statement.excluded.paused_question,
                        "paused_at_node": statement.excluded.paused_at_node,
                        "updated_at": statement.excluded.updated_at,
                    },
                )
            )

    async def pauses_for(self, task_ids: list[int]) -> dict[int, str]:
        """The reason each of these tasks was handed over, if one was stored.

        In bulk, because `_raise_hands` has a batch and asking per task is how a
        poll that usually finds nothing costs a query each every two seconds.
        """
        if not task_ids:
            return {}
        async with self._sessions() as session:
            rows = await session.execute(
                select(
                    schema.DagState.task_id, schema.DagState.paused_question
                ).where(
                    schema.DagState.task_id.in_(list(task_ids)),
                    schema.DagState.paused_question.is_not(None),
                )
            )
            return {task_id: question for task_id, question in rows}

    # ---- workflow graph state -------------------------------------------

    async def tasks_in_state(self, state: str, limit: int = 20) -> list[Task]:
        return await self._tasks(
            select(schema.Task)
            .where(schema.Task.state == state)
            .order_by(schema.Task.id)
            .limit(limit)
        )

    async def tasks(self, *, limit: int | None = None) -> list[Task]:
        return await self._tasks(
            select(schema.Task).order_by(schema.Task.id).limit(limit)
        )

    async def task(self, task_id: int) -> Task | None:
        """One task, freshest read — the pool's deps factory rebuilds a run's
        `Deps` from the current row, not a snapshot handed to an earlier pass."""
        async with self._sessions() as session:
            row = await session.get(schema.Task, task_id)
            return _task(row) if row is not None else None

    async def _tasks(self, query) -> list[Task]:
        async with self._sessions() as session:
            return [_task(row) for row in await session.scalars(query)]
