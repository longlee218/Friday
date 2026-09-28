"""The monitor repository — a `Database` mixin (ticket 16)."""

from __future__ import annotations

from friday.store._common import *  # noqa: F401,F403 (shared store internals)


class MonitorRepo:

    async def decisions(self) -> list[dict]:
        """Every triage decision, oldest first. The evidence for the threshold."""
        async with self._sessions() as session:
            rows = await session.scalars(
                # A decision, not a closed queue entry: context is stored with
                # a `triaged_at` so it never queues, but nothing judged it.
                select(schema.Message)
                .where(schema.Message.decision_type.is_not(None))
                .order_by(schema.Message.triaged_at)
            )
            return [
                {
                    "provider": row.provider,
                    "message_id": row.provider_message_id,
                    "type": row.decision_type,
                    "confidence": row.decision_confidence,
                    "params": row.decision_params or {},
                    "task_id": row.task_id,
                    "triaged_at": row.triaged_at,
                }
                for row in rows
            ]

    async def flow_for(
        self, *, provider: str, message_id: str
    ) -> MessageFlow | None:
        """Everything that followed from one message, in one request.

        **"One request", not "one instant", and the difference is deliberate.**
        D6 argues against joining in the browser because "four requests read four
        instants of a database being written to". This narrows that window to
        microseconds inside one process — the reads are `_calls_about` (one
        session, both tables), the outbound rows, the message and its task, and
        the turn — but SQLite in WAL gives each session its own snapshot, so it
        is not one atomic read and this docstring said it was.

        Left as several sessions on purpose. Making it atomic means threading a
        session through `turn_from` and the outbound reader, which are shared
        with callers that have no such need, and what is bought is a torn *debug
        view* rather than a wrong decision — nothing acts on this. The claim is
        corrected instead, which is the half that was actually wrong.

        One method rather than four calls a browser joins (D6): a task can
        change state between the second request and the third, and the path
        rendered would be one that never existed. `/api/board` is one
        aggregate for the same reason and says so.

        `None` only when there is no such message. A message nothing has
        triaged yet comes back with `decision=None`, which is a state — queued
        and unread — and not an absence.

        The two correlation keys are read separately and merged, because they
        are separate facts: triage's call names a message and no task, since
        no task existed when it ran; everything after names the task and not
        the message. Merging here is the join this exists to do — a caller
        handed two lists is a caller doing it again, differently.
        """
        async with self._sessions() as session:
            row = await session.get(schema.Message, (provider, message_id))
            if row is None:
                return None
            event = _event(row)
            task = await session.get(schema.Task, row.task_id) if row.task_id else None

        calls, tools = await self._calls_about(message_id, row.task_id)
        outbound = (
            await self._outbound_for_task(row.task_id)
            if row.task_id is not None
            else []
        )

        turn, _ = await self.turn_from(event)
        return MessageFlow(
            message=event,
            # `turn_from` shows only what it can attribute to the reporter, so
            # a message this process posted has an empty turn. Falling back to
            # the message itself keeps the path readable rather than showing a
            # flow whose first step is missing.
            turn=turn or [event],
            decision=(
                {
                    "type": row.decision_type,
                    "confidence": row.decision_confidence,
                    "params": row.decision_params or {},
                }
                if row.triaged_at is not None
                else None
            ),
            triaged_at=row.triaged_at,
            task=_task(task) if task is not None else None,
            # Already ordered by `_calls_about`, in SQL, with the row id as
            # the tiebreak — which is better than the sort that used to be
            # here: neither dataclass carries the id, so a Python sort had
            # only the timestamp and leaned on the merge order for ties.
            model_calls=calls,
            tool_calls=tools,
            outbound=outbound,
        )

    async def _calls_about(
        self, message_id: str, task_id: int | None
    ) -> tuple[list[ModelCall], list[ToolCall]]:
        """Every model and tool call that names this message *or* its task.

        `OR` in one query rather than two lists concatenated, and that is a
        correctness fix rather than a tidiness one. The first version read the
        two keys separately and appended, on the assumption that no call
        carries both — and nothing enforces that: `_About` in
        `friday/kernel/harness/retry.py` holds `message_id` and `task_id`
        independently, and `Harness.run`'s docstring invites both ("a caller
        supplies whichever it knows"). Only triage passes one today, so the
        assumption held by coincidence of the current call sites. The first
        node to pass both would have had every call rendered twice and its
        tokens counted twice on the task screen.

        Ordered here rather than by the caller, since the two kinds are one
        sequence: triage's call names the message and everything after names
        the task.

        **Known limitation: `message_id` is not scoped by provider.** Neither
        `model_calls` nor `tool_calls` carries a provider column — only the
        message table does, as half of its composite key — so a call is
        correlated by a bare id. `flow_for` takes a provider and uses it for
        the message lookup alone, which is honest today because there is one
        provider and Discord snowflakes do not collide with themselves. It
        stops being honest the day a second provider exists: two messages
        could share an id and each would show the other's calls. Recorded
        rather than fixed because the fix is a column and a migration, and
        the trigger is a change nobody has made.

        Recorded as a **tripwire**, not as this paragraph:
        `test_there_is_still_only_one_provider_name` fails the day a second
        provider name appears and says what it means for this method. A note
        naming a trigger condition is worth what the next person reading it
        is worth, and this repo's own convention is that the rules only
        written down are the ones that drifted.
        """
        by_key = lambda table: (  # noqa: E731
            (table.message_id == message_id) | (table.task_id == task_id)
            if task_id is not None
            else (table.message_id == message_id)
        )
        async with self._sessions() as session:
            calls = await session.scalars(
                select(schema.ModelCall)
                .where(by_key(schema.ModelCall))
                .order_by(schema.ModelCall.created_at.asc(), schema.ModelCall.id.asc())
            )
            model_calls = [_model_call(row) for row in calls]
            tools = await session.scalars(
                select(schema.ToolCall)
                .where(by_key(schema.ToolCall))
                .order_by(schema.ToolCall.created_at.asc(), schema.ToolCall.id.asc())
            )
            return model_calls, [_tool_call(row) for row in tools]

    async def _outbound_for_task(self, task_id: int) -> list[Outbound]:
        """Everything queued about one task, oldest first — the end of a path."""
        query = (
            select(schema.Outbound)
            .where(schema.Outbound.task_id == task_id)
            .order_by(schema.Outbound.id)
        )
        async with self._sessions() as session:
            return [_outbound(row) for row in await session.scalars(query)]

    async def counts(self) -> dict:
        """How much of everything there is, without materialising any of it."""
        async with self._sessions() as session:
            return {
                "messages": await session.scalar(
                    select(func.count()).select_from(schema.Message)
                ),
                "untriaged": await session.scalar(
                    select(func.count())
                    .select_from(schema.Message)
                    .where(
                        schema.Message.mention_type.is_not(None),
                        schema.Message.triaged_at.is_(None),
                    )
                ),
                "last_message_at": await session.scalar(
                    select(func.max(schema.Message.created_at))
                ),
                "tasks": dict(
                    (await session.execute(
                        select(schema.Task.state, func.count())
                        .group_by(schema.Task.state)
                    )).all()
                ),
                "outbound": dict(
                    (await session.execute(
                        select(schema.Outbound.state, func.count())
                        .group_by(schema.Outbound.state)
                    )).all()
                ),
            }

    async def running_tasks(self) -> list[RunningTask]:
        """Tasks that are still being worked in, with the two
        activity fields the Monitor screen renders (`last_activity_at`,
        `last_tool`, `attempts`).

        Three round trips folded into one:
        - tasks in `OPEN` states, ordered by id
        - max `created_at` and `count(*)` from `model_calls`
        - the most recent `tool_calls.tool` for each task

        Two batched subqueries rather than one query per task, and
        one parent query rather than the N+1 the BoardScreen's older
        per-task pattern would have produced."""
        open_states = [s.value for s in OPEN]
        async with self._sessions() as session:
            # Most recent activity + count from model_calls, per task.
            model_subq = (
                select(
                    schema.ModelCall.task_id.label("task_id"),
                    func.max(schema.ModelCall.created_at).label("last_at"),
                    func.count(schema.ModelCall.id).label("n"),
                )
                .where(schema.ModelCall.task_id.is_not(None))
                .group_by(schema.ModelCall.task_id)
                .subquery()
            )
            # Most recent tool_calls.tool per task.
            tool_subq = (
                select(
                    schema.ToolCall.task_id.label("task_id"),
                    func.max(schema.ToolCall.created_at).label("last_at"),
                )
                .where(schema.ToolCall.task_id.is_not(None))
                .group_by(schema.ToolCall.task_id)
                .subquery()
            )
            # The parent join: tasks left-join both. SQLite honours
            # `coalesce` on `datetime`, so the row is the newer of
            # the two timestamps (or `None`).
            # The `messages` subquery finds the message that opened
            # the task — the operator's drill-down from the Monitor
            # screen to the Flow screen needs the source message id,
            # not the conversation id. One round trip, no per-task
            # lookup.
            message_subq = (
                select(
                    schema.Message.task_id.label("task_id"),
                    schema.Message.provider.label("provider"),
                    schema.Message.provider_message_id.label("provider_message_id"),
                )
                .order_by(schema.Message.created_at.asc())
                .subquery()
            )
            stmt = (
                select(
                    schema.Task,
                    func.coalesce(model_subq.c.last_at, tool_subq.c.last_at).label(
                        "last_activity_at"
                    ),
                    func.coalesce(model_subq.c.n, 0).label("attempts"),
                    tool_subq.c.last_at.label("tool_last_at"),
                    message_subq.c.provider.label("msg_provider"),
                    message_subq.c.provider_message_id.label("msg_id"),
                )
                .where(schema.Task.state.in_(open_states))
                .outerjoin(model_subq, model_subq.c.task_id == schema.Task.id)
                .outerjoin(tool_subq, tool_subq.c.task_id == schema.Task.id)
                .outerjoin(
                    message_subq,
                    message_subq.c.task_id == schema.Task.id,
                )
                .order_by(
                    func.coalesce(model_subq.c.last_at, tool_subq.c.last_at).desc().nullslast()
                )
            )
            rows = await session.execute(stmt)

        # The two-name-from-one-row problem: the `last_tool` is on
        # `tool_calls` but we only fetched its timestamp. One more
        # round trip with the timestamps we have, to keep this
        # monitor query from growing into a join with the full tool
        # row.
        last_tool_by_task: dict[int, str] = {}
        if rows:
            ts_pairs = [
                (row[0].id, row.tool_last_at)
                for row in rows
                if row.tool_last_at is not None
            ]
            if ts_pairs:
                # Pick the most recent tool_call.tool for each task
                # whose last activity is the same timestamp the
                # `tool_subq` aggregated. Cheaper than a window
                # function on SQLite.
                tool_stmt = (
                    select(schema.ToolCall.task_id, schema.ToolCall.tool, schema.ToolCall.created_at)
                    .where(
                        schema.ToolCall.task_id.in_({tid for tid, _ in ts_pairs}),
                        schema.ToolCall.created_at.in_({ts for _, ts in ts_pairs}),
                    )
                )
                async with self._sessions() as session:
                    for tid, tool, _ in await session.execute(tool_stmt):
                        last_tool_by_task[tid] = tool

        return [
            RunningTask(
                id=row[0].id,
                type=row[0].type,
                state=row[0].state,
                room=str(row[0].conversation_id),
                # `provider:provider_message_id` is the shape `/flow/`
                # accepts. The Monitor screen reads this verbatim to
                # build the deep-link; the wire shape is the URL
                # shape by design.
                message_id=(
                    f"{row.msg_provider}:{row.msg_id}"
                    if row.msg_provider and row.msg_id
                    else None
                ),
                last_activity_at=row.last_activity_at,
                last_tool=last_tool_by_task.get(row[0].id),
                attempts=row.attempts or 0,
            )
            for row in rows
        ]

    async def recent_events(self, limit: int = 100) -> list[MonitorEvent]:
        """The Monitor screen's live feed: a single ordered list of
        the last `limit` rows across `model_calls` and `tool_calls`,
        newest first. Two queries, merged in Python — the union
        of two indexed-by-`created_at` tables is a SQL `UNION ALL`,
        which on SQLite with this many rows is faster as two
        indexed reads than one UNION."""
        async with self._sessions() as session:
            model_rows = await session.execute(
                select(
                    schema.ModelCall.id,
                    schema.ModelCall.agent,
                    schema.ModelCall.latency_ms,
                    schema.ModelCall.attempt,
                    schema.ModelCall.created_at,
                )
                .order_by(schema.ModelCall.created_at.desc())
                .limit(limit)
            )
            tool_rows = await session.execute(
                select(
                    schema.ToolCall.id,
                    schema.ToolCall.agent,
                    schema.ToolCall.tool,
                    schema.ToolCall.latency_ms,
                    schema.ToolCall.failed,
                    schema.ToolCall.created_at,
                )
                .order_by(schema.ToolCall.created_at.desc())
                .limit(limit)
            )

        events: list[MonitorEvent] = []
        for row in model_rows:
            attempt = row.attempt or 1
            state = "retrying" if attempt > 1 else "done"
            events.append(
                MonitorEvent(
                    id=row.id,
                    type="model_call",
                    occurred_at=row.created_at,
                    agent=row.agent,
                    tool=None,
                    latency_ms=row.latency_ms,
                    state=state,
                )
            )
        for row in tool_rows:
            events.append(
                MonitorEvent(
                    id=row.id,
                    type="tool_call",
                    occurred_at=row.created_at,
                    agent=row.agent,
                    tool=row.tool,
                    latency_ms=row.latency_ms,
                    state="ok" if not row.failed else "failed",
                )
            )
        events.sort(key=lambda e: e.occurred_at, reverse=True)
        return events[:limit]

    async def monitor_snapshot(self) -> MonitorSnapshot:
        """One snapshot of what the Monitor screen asks for on mount.
        Counts come from the same queries the BoardScreen's footer
        uses; events and running_tasks come from `recent_events`
        and `running_tasks`. Spend is the day's total."""
        counts = await self.counts()
        events = await self.recent_events(limit=100)
        running = await self.running_tasks()
        spend_by_agent = await self.spent_today_by_agent()
        return MonitorSnapshot(
            status="connected",
            events=events,
            running_tasks=running,
            messages=counts["messages"],
            untriaged=counts["untriaged"],
            last_message_at=counts["last_message_at"],
            spend_today=sum(spend_by_agent.values()),
        )
