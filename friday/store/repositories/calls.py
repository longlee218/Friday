"""The calls repository — a `Database` mixin (ticket 16)."""

from __future__ import annotations

from friday.store._common import *  # noqa: F401,F403 (shared store internals)


class CallsRepo:

    # ---- model calls ---------------------------------------------------

    async def record_model_call(self, **values) -> None:
        values.setdefault("created_at", _now())
        async with self._sessions.begin() as session:
            session.add(schema.ModelCall(**values))
        # Publish on the bus so the SSE stream sees the event the
        # moment the row is written. The payload carries the row
        # id and the agent the screen renders; the SSE endpoint
        # serialises it as JSON. The store does not wait for the
        # bus — `publish` is sync and never blocks — so a slow
        # subscriber cannot stall a model call.
        from friday.kernel.ops.events import get_bus
        task_id = values.get("task_id")
        message_id: str | None = None
        if task_id is not None:
            src = await self.source_message_of_task(int(task_id))
            if src is not None:
                provider, mid = src
                message_id = f"{provider}:{mid}"
        get_bus().publish(
            type_="model_call",
            payload={
                "row_id": values.get("id"),
                "agent": values.get("agent"),
                "task_id": task_id,
                "latency_ms": values.get("latency_ms"),
                "attempt": values.get("attempt") or 1,
                "message_id": message_id,
            },
        )

    async def record_tool_call(self, **values) -> None:
        values.setdefault("created_at", _now())
        async with self._sessions.begin() as session:
            session.add(schema.ToolCall(**values))
        from friday.kernel.ops.events import get_bus
        task_id = values.get("task_id")
        message_id: str | None = None
        if task_id is not None:
            src = await self.source_message_of_task(int(task_id))
            if src is not None:
                provider, mid = src
                message_id = f"{provider}:{mid}"
        get_bus().publish(
            type_="tool_call",
            payload={
                "row_id": values.get("id"),
                "agent": values.get("agent"),
                "tool": values.get("tool"),
                "task_id": task_id,
                "failed": values.get("failed", False),
                "latency_ms": values.get("latency_ms"),
                "message_id": message_id,
            },
        )

    async def record_node_run(self, **values) -> None:
        """One attempt at one graph node — see `schema.NodeRun`.

        `reason` is scrubbed at the write for the reason `fail_outbound`'s
        error is: it is exception text, and a client's exception can quote
        the header it sent."""
        values.setdefault("created_at", _now())
        values["reason"] = scrub(values.get("reason") or "")
        async with self._sessions.begin() as session:
            session.add(schema.NodeRun(**values))
        # A per-workflow progress event on the same bus the model/tool calls
        # use (ticket 08): one node of a graph finished, so the board's workflow
        # panel refreshes its DBOS snapshot. Sync and non-blocking like the
        # others, so a slow SSE subscriber cannot stall a node's recording.
        from friday.kernel.ops.events import get_bus
        get_bus().publish(
            type_="workflow",
            payload={
                "task_id": values.get("task_id"),
                "dag_name": values.get("dag_name"),
                "node": values.get("node"),
                "status": values.get("status"),
                "attempt": values.get("attempt"),
            },
        )

    async def node_runs(self, task_id: int) -> list[dict]:
        """Every recorded attempt at every node of this task's graphs, oldest
        first."""
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.NodeRun)
                .where(schema.NodeRun.task_id == task_id)
                .order_by(schema.NodeRun.id)
            )
            return [
                {
                    "dag_name": r.dag_name,
                    "node": r.node,
                    "attempt": r.attempt,
                    "status": r.status,
                    "reason": r.reason,
                    "duration_ms": r.duration_ms,
                    "created_at": r.created_at,
                }
                for r in rows
            ]

    async def source_message_of_task(self, task_id: int) -> tuple[str, str] | None:
        """`provider, provider_message_id` of the message that opened
        `task_id`. The SSE payload carries the row id of the call
        or tool, not the message; this lookup is what gives the
        Monitor screen the deep-link to the originating flow page.

        Returns `None` for tasks that pre-date the messages
        table having a `task_id` column, or for tasks that have
        no message attached (a manually-seeded plan)."""
        async with self._sessions() as session:
            result = await session.execute(
                select(
                    schema.Message.provider,
                    schema.Message.provider_message_id,
                )
                .where(schema.Message.task_id == task_id)
                .order_by(*_OLDEST_FIRST)
                .limit(1)
            )
            row = result.first()
            if row is None:
                return None
            return row[0], row[1]

    async def opening_messages(self, task_ids) -> dict[int, dict[str, str]]:
        """The message that opened each of these tasks — the earliest one
        linked to it — as `provider`, `provider_message_id` and `text`.

        One query for the lot, like `tools_for_tasks`: the board renders up
        to two hundred tasks but loads only the newest messages, so a card
        cannot find its own opening message among those (the operator's
        report, 2026-09-18). A task with no linked message is absent.
        """
        wanted = list(task_ids)
        if not wanted:
            return {}
        async with self._sessions() as session:
            rows = await session.execute(
                select(
                    schema.Message.task_id,
                    schema.Message.provider,
                    schema.Message.provider_message_id,
                    schema.Message.text,
                )
                .where(schema.Message.task_id.in_(wanted))
                .order_by(schema.Message.task_id, *_OLDEST_FIRST)
            )
            found: dict[int, dict[str, str]] = {}
            for task_id, provider, message_id, text in rows:
                found.setdefault(
                    task_id,
                    {"provider": provider, "provider_message_id": message_id, "text": text},
                )
            return found

    async def tools_for_tasks(self, task_ids) -> dict[int, list[ToolCall]]:
        """What each of these tasks reached for, oldest first within a task.

        One query rather than one per task, for the reason `calls_for_tasks`
        gives: the board renders up to two hundred of them.
        """
        wanted = list(task_ids)
        if not wanted:
            return {}
        query = (
            select(schema.ToolCall)
            .where(schema.ToolCall.task_id.in_(wanted))
            .order_by(schema.ToolCall.created_at.asc(), schema.ToolCall.id.asc())
        )
        grouped: dict[int, list[ToolCall]] = {}
        async with self._sessions() as session:
            for row in await session.scalars(query):
                if row.task_id is None:  # excluded by the filter; narrows the type
                    continue
                grouped.setdefault(row.task_id, []).append(_tool_call(row))
        return grouped

    async def spent_today(self, agent: str | None = None) -> int:
        """Tokens spent since midnight UTC, in and out — by one agent, or by
        all of them when no name is given.

        Summed from the rows rather than counted in memory, so a restart does
        not forgive a budget and the number cannot drift from what the board
        shows. Per agent because they are different jobs against different
        models: the classifier running on every mention and the responder
        running on a few are not one pool, and a shared ceiling would let the
        cheap high-volume one exhaust the careful one.

        Midnight UTC rather than the operator's midnight. A budget needs a
        boundary that does not move, and the process has no opinion about
        where they are.
        """
        start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
        query = select(
            func.sum(schema.ModelCall.input_tokens + schema.ModelCall.output_tokens)
        ).where(schema.ModelCall.created_at >= start)
        if agent is not None:
            query = query.where(schema.ModelCall.agent == agent)
        async with self._sessions() as session:
            return int(await session.scalar(query) or 0)

    async def spent_today_by_agent(self) -> dict[str, int]:
        """Today's tokens, per agent, in one query.

        Grouped from the rows rather than asked once per name, because there
        is no list of names to ask for: which agents exist is `config.yaml`'s
        business, and a hardcoded list here would be wrong the first time
        somebody adds one.
        An agent that has not spent anything today is simply absent, which is
        the same answer as zero and does not require knowing it exists.
        """
        start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
        query = (
            select(
                schema.ModelCall.agent,
                func.sum(
                    schema.ModelCall.input_tokens + schema.ModelCall.output_tokens
                ),
            )
            .where(schema.ModelCall.created_at >= start)
            .group_by(schema.ModelCall.agent)
        )
        async with self._sessions() as session:
            rows = await session.execute(query)
            return {agent: int(total or 0) for agent, total in rows}

    async def calls_for_tasks(self, task_ids) -> dict[int, list[ModelCall]]:
        """Every call for each of these tasks, oldest first within a task.

        One query rather than one per task: the board renders up to two hundred
        of them, and asking per task made a page render cost two hundred round
        trips to answer a question about a table that is indexed on exactly
        this column.
        """
        wanted = list(task_ids)
        if not wanted:
            return {}
        query = (
            select(schema.ModelCall)
            .where(schema.ModelCall.task_id.in_(wanted))
            .order_by(schema.ModelCall.created_at.asc(), schema.ModelCall.id.asc())
        )
        grouped: dict[int, list[ModelCall]] = {}
        async with self._sessions() as session:
            for row in await session.scalars(query):
                if row.task_id is None:  # excluded by the filter; narrows the type
                    continue
                grouped.setdefault(row.task_id, []).append(_model_call(row))
        return grouped

    async def calls_for_task(self, task_id: int) -> list[ModelCall]:
        """Every call made while working on one task, oldest first.

        Oldest first because they read as a sequence — what was extracted,
        then what was drafted — and a reader following a task's history is
        going forwards.
        """
        query = (
            select(schema.ModelCall)
            .where(schema.ModelCall.task_id == task_id)
            .order_by(schema.ModelCall.created_at.asc(), schema.ModelCall.id.asc())
        )
        async with self._sessions() as session:
            return [_model_call(row) for row in await session.scalars(query)]

    async def calls_by_message(self, message_ids) -> dict[str, ModelCall]:
        """The most recent call about each of these messages.

        Asked for *by message* rather than by taking a page of recent calls and
        keying it: a page is shared by every agent, and only triage's rows
        carry a message id at all. Once the extractors, the responder and the
        summariser started recording, a page of the newest calls could be
        entirely rows that can never match a message while the call that
        classified it sat just outside the window.

        Newest wins where a message has more than one — a reclassification is
        what the board should show.
        """
        wanted = list(message_ids)
        if not wanted:
            return {}
        query = (
            select(schema.ModelCall)
            .where(schema.ModelCall.message_id.in_(wanted))
            .order_by(schema.ModelCall.created_at.asc(), schema.ModelCall.id.asc())
        )
        async with self._sessions() as session:
            return {
                row.message_id: _model_call(row)
                for row in await session.scalars(query)
                # Excluded by the filter above; stated so the type says it too.
                if row.message_id is not None
            }

    async def model_calls(
        self,
        *,
        message_id: str | None = None,
        #: Only the rows that name no message. Distinct from `message_id=None`,
        #: which means "do not filter" — and the difference is the whole reason
        #: this exists: after every agent started recording, most rows name no
        #: message and there was no way to ask for them.
        uncorrelated: bool = False,
        #: `None` is unbounded, the same spelling `outbound` already uses.
        #: Every HTTP caller passes a number — the routes cap it at `MAX_PAGE`
        #: — and the one caller that does not is `flow_for`, which is already
        #: narrowed to a single message and must not silently truncate the
        #: path it exists to assemble.
        limit: int | None = 50,
    ) -> list[ModelCall]:
        query = select(schema.ModelCall)
        if message_id is not None:
            query = query.where(schema.ModelCall.message_id == message_id)
        elif uncorrelated:
            query = query.where(schema.ModelCall.message_id.is_(None))
        async with self._sessions() as session:
            rows = await session.scalars(
                query.order_by(schema.ModelCall.created_at.desc(),
                               schema.ModelCall.id.desc()).limit(limit)
            )
            return [
                _model_call(row)
                for row in rows
            ]

    async def trim_model_calls(self, *, keep_days: float) -> int:
        """Prompts are large and nobody reads old ones. A container that never
        restarts would otherwise fill its volume with them."""
        async with self._sessions.begin() as session:
            result = await session.execute(
                delete(schema.ModelCall).where(
                    schema.ModelCall.created_at < _now() - timedelta(days=keep_days)
                )
            )
            return result.rowcount
