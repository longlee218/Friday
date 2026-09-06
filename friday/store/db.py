"""The only store.

A deep module: every caller sees domain dataclasses, and nothing above this
seam knows SQLAlchemy exists. Mapped classes live in `friday.schema` and are
converted at the edge, so `friday.models` stays free of persistence concerns.

Never call this from a sync path — a blocking database call on the event loop
stalls ingestion.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    Integer,
    cast,
    delete,
    event,
    func,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import aliased
from sqlalchemy.pool import StaticPool

from friday.store import schema
from friday.domain.conversation import ConversationId
from friday.domain.states import OutboundState
from friday.domain.models import (
    Memory,
    MemoryScope,
    InboundEvent,
    MentionType,
    MessageFlow,
    ModelCall,
    ToolCall,
    Outbound,
    Task,
)
from friday.ops.redact import scrub
from friday.domain.states import OPEN, IllegalTransition, TaskState, may_move

__all__ = ["Database"]

log = logging.getLogger(__name__)

#: The types the classifier can actually produce — one per tool it has.
#: Anything else in `decision_type` is a state, not a classification.
CLASSIFIABLE = ("api_issue", "access_request", "doc_question", "skip")

_OLDEST_FIRST = (schema.Message.created_at, schema.Message.provider_message_id)
#: Ties break on the id cast as a number — two messages can share a timestamp,
#: and a snowflake is the platform's own answer to which came first.
_NEWEST_FIRST = (
    schema.Message.created_at.desc(),
    cast(schema.Message.provider_message_id, Integer).desc(),
)

#: Aliases, not definitions — `friday/domain/tasks.py` owns these. Kept as
#: local names because they read better in a `WHERE` clause than the enum
#: does, and renamed away from the enum's members so nothing here can quietly
#: become a second source.
OUTBOUND_QUEUED = OutboundState.QUEUED
OUTBOUND_SENT = OutboundState.SENT
OUTBOUND_FAILED = OutboundState.FAILED
OUTBOUND_SENT_MANUALLY = OutboundState.SENT_MANUALLY

#: Kinds the outbox refuses to select without an approval on the task. Kept as
#: data here because it is a `WHERE` clause; `friday.outbox.Kind` is where the
#: reasoning lives.
_NEEDS_APPROVAL = ("reply",)


def _engine(path: str):
    """One engine per process. In-memory needs `StaticPool`: without it every
    checkout opens a *different* empty database."""
    if path == ":memory:":
        return create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _wal(connection, _record):
        connection.execute("PRAGMA journal_mode=WAL")

    return engine


class Database:
    def __init__(self, engine, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._engine = engine
        self._sessions = sessions

    @classmethod
    async def connect(cls, path: str, *, create: bool = False) -> "Database":
        """Open the store. `create` builds the schema straight from the models.

        Off by default, because a real database gets its shape from Alembic and
        `run_agent.migrate()` has already run by the time this is called.
        Building tables here as well would hide a forgotten revision: every
        fresh database would work, and only the one that already exists would
        break. Tests opt in; nothing else should.
        """
        engine = _engine(path)
        if create:
            async with engine.begin() as connection:
                await connection.run_sync(schema.Base.metadata.create_all)
        return cls(engine, async_sessionmaker(engine, expire_on_commit=False))

    async def close(self) -> None:
        await self._engine.dispose()

    # ---- messages ------------------------------------------------------

    async def record_message(
        self, event: InboundEvent, *, context_only: bool = False
    ) -> bool:
        """Store a message. False if this one was already recorded.

        The primary key is (provider, provider_message_id), which is what makes
        the two delivery paths safe to run concurrently.

        `context_only` keeps a message without ever classifying it. Such a
        message may well carry a mention type — our own replies in a DM always
        do — so the mention type alone cannot decide what belongs in the queue.
        Two kinds arrive this way: history seeded from before a conversation
        involved us, and messages the inbox ruled out of scope but kept because
        a conversation missing half of itself does not read.
        """
        statement = insert(schema.Message).values(
            provider=event.provider,
            provider_message_id=event.provider_message_id,
            channel_id=event.channel_id,
            thread_id=event.thread_id,
            conversation_id=str(event.conversation),
            author_id=event.author_id,
            author_name=event.author_name,
            text=event.text,
            original_text=event.text,
            created_at=event.created_at,
            is_own=event.is_own,
            mention_type=event.mention_type.value if event.mention_type else None,
            reply_to=event.reply_to,
            triaged_at=_now() if context_only else None,
        )
        async with self._sessions.begin() as session:
            result = await session.execute(statement.on_conflict_do_nothing())
            return result.rowcount == 1

    async def messages(
        self, conversation: ConversationId | None = None, *, limit: int | None = None
    ) -> list[InboundEvent]:
        """Messages in chronological order, most recent `limit` of them.

        Bounded because this is what a model is given as context: unbounded, the
        prompt grows with the channel and a long-running conversation eventually
        costs more than it explains.
        """
        query = select(schema.Message)
        if conversation is not None:
            query = query.where(schema.Message.conversation_id == str(conversation))
        if limit is None:
            return await self._events(query.order_by(*_OLDEST_FIRST))
        newest = await self._events(query.order_by(*_NEWEST_FIRST).limit(limit))
        return list(reversed(newest))

    async def relevant_messages(
        self, conversation: ConversationId
    ) -> list[InboundEvent]:
        """What concerns the operator in this conversation: mentions them, was
        written by them, or replies to something they wrote. Chronological,
        unbounded.

        This is context assembled *for a call*, not what gets stored — a
        message thrown away at write time can never be reconsidered under a
        better definition of relevant later, so nothing here is deleted, only
        read selectively. Unbounded rather than the last N: a sliding window
        changes on every call, so nothing before it can ever be cached; the
        structural filter already keeps a busy channel's unrelated traffic out,
        so a message once relevant stays part of the prefix forever and only
        the tail grows as the conversation continues.
        """
        return await self._events(
            self._relevant(schema.Message.conversation_id == str(conversation))
            .order_by(*_OLDEST_FIRST)
        )

    async def relevant_messages_in_channel(
        self, provider: str, channel_id: str
    ) -> list[InboundEvent]:
        """The same filter, scoped to a whole channel rather than one
        conversation.

        A thread is its own conversation (see `friday.conversation`) — reading
        by `conversation_id` alone would miss every reply happening inside one.
        A channel-level summary needs everything under the channel, threads
        included, which is what `channel_id` — a separate column every message
        under it shares — gives directly.
        """
        return await self._events(
            self._relevant(
                schema.Message.provider == provider,
                schema.Message.channel_id == channel_id,
            ).order_by(*_OLDEST_FIRST)
        )

    def _relevant(self, *scope):
        own = select(schema.Message.provider_message_id).where(
            *scope, schema.Message.is_own.is_(True)
        )
        return select(schema.Message).where(
            *scope,
            or_(
                schema.Message.mention_type.is_not(None),
                schema.Message.is_own.is_(True),
                schema.Message.reply_to.in_(own),
            ),
        )

    async def page_messages(
        self, *, limit: int = 50, before: str | None = None
    ) -> list[InboundEvent]:
        """A page of the feed, newest first.

        Keyset rather than offset: the table is written to constantly, and an
        offset would skip or repeat rows as it grows underneath the reader.
        """
        query = select(schema.Message)
        if before is not None:
            query = query.where(
                cast(schema.Message.provider_message_id, Integer)
                < cast(literal(before), Integer)
            )
        return await self._events(query.order_by(*_NEWEST_FIRST).limit(limit))

    async def mentions(self) -> list[InboundEvent]:
        """Only the messages that addressed us."""
        return await self._events(
            select(schema.Message)
            .where(schema.Message.mention_type.is_not(None))
            .order_by(schema.Message.created_at, schema.Message.provider_message_id)
        )

    async def turn_from(self, event: InboundEvent) -> tuple[list[InboundEvent], bool]:
        """The turn this message opens: everything the same person said in the
        same conversation from here on, up to the first message by somebody
        else. Returns the messages and whether somebody else has since spoken.

        Worked out when read, not stored. At the moment a message arrives it is
        not known whether the turn is over — the next message is three seconds
        away and has not happened — so a stored turn id would be wrong for as
        long as the turn is still running.

        What this process posted is left out: in a channel the operator tests
        in, the account is both sides of the conversation.
        """
        ours = select(schema.Outbound.sent_message_id).where(
            schema.Outbound.sent_message_id.is_not(None)
        )
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Message)
                .where(
                    schema.Message.conversation_id == str(event.conversation),
                    schema.Message.created_at >= event.created_at,
                    schema.Message.provider_message_id.not_in(ours),
                )
                .order_by(schema.Message.created_at)
            )
            turn: list[InboundEvent] = []
            for row in rows:
                if row.author_id != event.author_id:
                    return turn, True
                turn.append(_event(row))
            return turn, False

    async def untriaged_mentions(self, limit: int = 50) -> list[InboundEvent]:
        """The queue: work nobody has looked at, oldest first.

        Context shares the table but is never classified — the queue is a
        `WHERE` clause, not a second table. `context_only` stamps `triaged_at`
        on the way in, so that one column carries the whole answer.

        It used to say `mention_type IS NOT NULL` as well, which was the same
        question asked a second way and by a worse proxy. The two agreed until
        a reply to one of our own messages became work: it addresses us and
        mentions nobody, so the inbox let it in and this threw it away. The
        agent asked a question, the reporter answered, and the answer sat in
        the table having been accepted and never queued.

        The inbox decides what is work. This reads that decision; it does not
        take it again.
        """
        return await self._events(
            select(schema.Message)
            .where(schema.Message.triaged_at.is_(None))
            .order_by(schema.Message.created_at)
            .limit(limit)
        )

    async def mark_triaged(
        self,
        event: InboundEvent,
        task_id: int | None = None,
        *,
        decision: dict | None = None,
    ) -> None:
        """Close the queue entry, and keep what triage concluded about it."""
        decision = decision or {}
        async with self._sessions.begin() as session:
            await session.execute(
                update(schema.Message)
                .where(
                    schema.Message.provider == event.provider,
                    schema.Message.provider_message_id == event.provider_message_id,
                )
                .values(
                    triaged_at=_now(),
                    task_id=task_id,
                    decision_type=decision.get("type"),
                    decision_confidence=decision.get("confidence"),
                    decision_params=decision.get("params", {}),
                )
            )

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

    async def _events(self, query) -> list[InboundEvent]:
        async with self._sessions() as session:
            return [_event(row) for row in await session.scalars(query)]

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
        `friday/agent/harness.py` holds `message_id` and `task_id`
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

    # ---- model calls ---------------------------------------------------

    async def record_model_call(self, **values) -> None:
        values.setdefault("created_at", _now())
        async with self._sessions.begin() as session:
            session.add(schema.ModelCall(**values))

    async def record_tool_call(self, **values) -> None:
        values.setdefault("created_at", _now())
        async with self._sessions.begin() as session:
            session.add(schema.ToolCall(**values))

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
        business — `extractor_<type>` alone is one per task type — and a
        hardcoded list here would be wrong the first time somebody adds one.
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

    async def tone_examples(self, limit: int = 8) -> list[InboundEvent]:
        """The operator's own recent messages, for a model to learn a voice from.

        Excludes anything the agent sent: it goes out under the same account
        and comes back over the gateway indistinguishable from a real one, so
        without this the responder learns its own voice and amplifies it every
        round. Real examples carry a tone that a written style guide does not,
        which is the whole reason for reading them.
        """
        sent = select(schema.Outbound).where(
            schema.Outbound.state.in_((OUTBOUND_SENT, OUTBOUND_SENT_MANUALLY))
        ).subquery()
        our_ids = select(sent.c.sent_message_id).where(
            sent.c.sent_message_id.is_not(None)
        )
        # Matching the text too, because an id is not guaranteed: `send` may
        # return none, and rows sent before it did have none at all. An
        # operator echoing back the agent's own sentence is not their voice
        # either, so a false positive here costs nothing.
        our_words = select(sent.c.text)
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Message)
                .where(
                    schema.Message.is_own.is_(True),
                    schema.Message.provider_message_id.not_in(our_ids),
                    schema.Message.text.not_in(our_words),
                )
                # Tie-broken on the id, cast as a number: two messages can
                # share a timestamp, and a snowflake is the platform's own
                # answer to which came first. Same reason `advance_cursor`
                # casts rather than comparing text.
                .order_by(
                    schema.Message.created_at.desc(),
                    cast(schema.Message.provider_message_id, Integer).desc(),
                )
                .limit(limit)
            )
            return list(reversed([_event(row) for row in rows]))

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

    # ---- memory ----------------------------------------------------------
    #
    # Ticket 09's D9: an agent writes its own memory and reads it back,
    # scoped to one channel by `MemoryScope`. What replaced the old
    # staging-and-promotion tier is enforced here, not in the tool layer —
    # the tool relays whatever it gets back, so a check that only lived there
    # would not survive a second caller.

    #: How many memories one channel may hold. Enforced here rather than in
    #: `friday/tools/memory.py`, which is the whole point: a store method is
    #: the only place that can actually stop a write, and a tool that merely
    #: checked would not survive a second caller reaching the store directly.
    #:
    #: At the cap, a write is refused rather than evicting the oldest row.
    #: Silently dropping *any* memory — oldest or not — is exactly the kind
    #: of loss this design otherwise refuses to produce without a trace: the
    #: operator can see what was written and by whom, but only because
    #: nothing else removes it first. Two hundred is a lot of one-sentence
    #: facts about one room; hitting it is itself a signal that something
    #: should be corrected or removed on purpose, which
    #: `memory_update`/`memory_delete` exist for.
    MEMORY_PER_CHANNEL = 200

    #: How long one memory's text may be, in characters. Same reasoning as
    #: `MEMORY_PER_CHANNEL`, for the same reason: `friday/tools/memory.py`
    #: already cuts to this length before writing, and a tool that merely
    #: checked would not survive a second caller reaching `memory_add` or
    #: `memory_update` directly. Cut, not refused, matching the tool's own
    #: policy (`_bounded`'s docstring) — a refusal here would cost a turn for
    #: nothing when the model has already said what it meant.
    TEXT_CHARS = 500

    async def memory_search(
        self, scope: MemoryScope, query: str, *, limit: int
    ) -> list[Memory]:
        """Every match in this channel, newest first — not ranked by how well
        it matches, only by when it was written.

        `query` is matched the way `SkillLibrary.search` matches one of its
        ranks — every word has to appear somewhere in the text — but this
        does not rank: `SkillLibrary` scores a fixed catalogue read at
        startup, and a channel's memory changes underneath every call, so
        recency is the cheap, honest order rather than a score this method
        does not compute. Over a corpus that is a handful of rows per channel
        today; revisit if a room's memory ever grows past what a linear scan
        over its own rows can do cheaply.

        An empty `query` matches every word-count check vacuously and returns
        the channel's most recent memories up to `limit` — not validated
        against, because a model sending "" is a model that wants to see what
        is there, and that is a reasonable thing to want.
        """
        words = [w for w in query.lower().split() if w]
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Memory)
                .where(
                    schema.Memory.channel_id == scope.channel_id,
                    schema.Memory.deleted_at.is_(None),
                )
                .order_by(schema.Memory.created_at.desc())
            )
            matched = [
                row
                for row in rows
                if all(word in row.text.lower() for word in words)
            ]
            return [_memory(row) for row in matched[:limit]]

    async def memory_add(self, scope: MemoryScope, text: str) -> Memory | None:
        """Write a new memory, or refuse if the channel is already full.

        `None` means the channel is at `MEMORY_PER_CHANNEL` — the caller
        (`friday/tools/memory.py`) turns that into a message the model can
        act on, the same way it turns a wrong-scope id into one.

        The count and the insert are not one atomic check-and-set: two calls
        for the same channel racing between the `await` on the count and the
        write could both land under the cap. Not a bound this method enforces
        itself, because nothing today makes that race possible — `Pool`
        drains pending tasks one at a time (`for task in ...: await
        self._act(task)`), so only one `Responder.draft()`, the tool's only
        caller, is ever in flight. It becomes a real question the day a
        second memory-tool-bearing agent runs concurrently with the pool's
        loop, which is not true of anything wired today.
        """
        async with self._sessions.begin() as session:
            count = await session.scalar(
                select(func.count()).select_from(schema.Memory).where(
                    schema.Memory.channel_id == scope.channel_id,
                    schema.Memory.deleted_at.is_(None),
                )
            )
            if (count or 0) >= self.MEMORY_PER_CHANNEL:
                return None
            now = _now()
            row = schema.Memory(
                id=_memory_id(),
                channel_id=scope.channel_id,
                agent=scope.agent,
                text=text[: self.TEXT_CHARS],
                task_id=scope.task_id,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            await session.flush()
            return _memory(row)

    async def memory_update(
        self, scope: MemoryScope, memory_id: str, text: str
    ) -> Memory | None:
        """Replace a memory's text in place, or `None` if this scope has no
        such (live) memory by this id — wrong channel, never existed, and
        already deleted all read the same, on purpose."""
        async with self._sessions.begin() as session:
            row = await self._live_memory(session, scope, memory_id)
            if row is None:
                return None
            row.text = text[: self.TEXT_CHARS]
            row.updated_at = _now()
            await session.flush()
            return _memory(row)

    async def memory_delete(self, scope: MemoryScope, memory_id: str) -> bool:
        """Soft-delete: the row survives with who removed it and when, so an
        operator can see what a line said after it is gone. `False` for the
        same three cases `memory_update` treats alike."""
        async with self._sessions.begin() as session:
            row = await self._live_memory(session, scope, memory_id)
            if row is None:
                return False
            row.deleted_at = _now()
            row.deleted_by = scope.agent
            return True

    async def memories_for_channel(
        self, channel_id: str, *, limit: int = 200
    ) -> list[Memory]:
        """This channel's memories, live or deleted, newest first — the
        operator's view. Not scope-filtered by agent: this is a human looking
        at one room, not a tool call from inside it.

        Bounded, unlike the room a caller might expect this to have: live
        memories are bounded by `MEMORY_PER_CHANNEL`, but a deleted row is
        never purged, so a channel that has churned through many corrections
        holds an unbounded number of rows this method would otherwise return
        every one of.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Memory)
                .where(schema.Memory.channel_id == channel_id)
                .order_by(schema.Memory.created_at.desc())
                .limit(limit)
            )
            return [_memory(row) for row in rows]

    async def _live_memory(self, session, scope: MemoryScope, memory_id: str):
        """The row, if it exists, belongs to this scope, and is not deleted —
        the one query `memory_update` and `memory_delete` share, so the three
        reasons an id can fail to resolve cannot drift apart between them."""
        return await session.scalar(
            select(schema.Memory).where(
                schema.Memory.id == memory_id,
                schema.Memory.channel_id == scope.channel_id,
                schema.Memory.deleted_at.is_(None),
            )
        )

    # ---- cursors -------------------------------------------------------

    async def advance_cursor(self, event: InboundEvent) -> None:
        """Move a channel's cursor forward to this message, never backward.

        Ids are numeric snowflakes, so age is a numeric comparison — as text,
        '99' would sort after '100' and look newer.
        """
        statement = insert(schema.Cursor).values(
            provider=event.provider,
            channel_id=event.channel_id,
            last_seen_message_id=event.provider_message_id,
        )
        async with self._sessions.begin() as session:
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[schema.Cursor.provider, schema.Cursor.channel_id],
                    set_={
                        "last_seen_message_id": statement.excluded.last_seen_message_id
                    },
                    where=cast(statement.excluded.last_seen_message_id, Integer)
                    > cast(schema.Cursor.last_seen_message_id, Integer),
                )
            )

    async def cursor_for(self, provider: str, channel_id: str) -> str | None:
        async with self._sessions() as session:
            return await session.scalar(
                select(schema.Cursor.last_seen_message_id).where(
                    schema.Cursor.provider == provider,
                    schema.Cursor.channel_id == channel_id,
                )
            )

    # ---- conversations -------------------------------------------------

    async def conversation_is_tracked(self, event: InboundEvent) -> bool:
        async with self._sessions() as session:
            return await session.scalar(
                select(schema.Conversation.id).where(
                    schema.Conversation.id == str(event.conversation)
                )
            ) is not None

    async def record_conversation(self, event: InboundEvent) -> bool:
        """Ensure the conversation exists. True if this created it."""
        statement = insert(schema.Conversation).values(id=str(event.conversation))
        async with self._sessions.begin() as session:
            result = await session.execute(statement.on_conflict_do_nothing())
            return result.rowcount == 1

    async def conversations(self) -> list[ConversationId]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Conversation.id).order_by(schema.Conversation.id)
            )
            return [ConversationId.parse(row) for row in rows]

    # ---- outbox --------------------------------------------------------

    async def queue_outbound(
        self,
        *,
        task_id: int | None,
        conversation: ConversationId,
        kind: str,
        sender: str,
        text: str,
        reply_to: str | None = None,
    ) -> Outbound:
        row = schema.Outbound(
            task_id=task_id,
            conversation_id=str(conversation),
            kind=str(kind),
            sender=sender,
            text=text,
            reply_to=reply_to,
            state=OUTBOUND_QUEUED,
            created_at=_now(),
        )
        async with self._sessions.begin() as session:
            session.add(row)
        return _outbound(row)

    async def sendable_outbound(self, limit: int = 20) -> list[Outbound]:
        """Queued rows that are allowed out, oldest first.

        The approval check lives here rather than in the sender, so a caller
        cannot forget it: a kind that needs approval is simply not selected
        until its task has one. `attempts` orders after `id` so a row that keeps
        failing does not monopolise every batch.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Outbound)
                # Outer, so a row with no task — an alert about the system
                # itself — is still selected rather than dropped by the join.
                .outerjoin(schema.Task, schema.Task.id == schema.Outbound.task_id)
                .where(
                    schema.Outbound.state == OUTBOUND_QUEUED,
                    or_(
                        schema.Outbound.retry_after.is_(None),
                        schema.Outbound.retry_after <= _now(),
                    ),
                    or_(
                        schema.Outbound.kind.not_in(_NEEDS_APPROVAL),
                        schema.Task.approved_at.is_not(None),
                    ),
                )
                .order_by(schema.Outbound.attempts, schema.Outbound.id)
                .limit(limit)
            )
            return [_outbound(row) for row in rows]

    async def we_sent(self, provider: str, provider_message_id: str, text: str) -> bool:
        """Whether this message is one this agent put there itself.

        Not the same question as "is it from the watched account". The operator
        types from that account too, and a self-mention is how the pipeline is
        tested without a second person. What must never create work is a
        message *this process posted* — answering that is answering itself, and
        it does not stop.

        Matched on the id **or** the text. The id is the precise answer and it
        is not always available in time: the outbox posts, then records the id
        it got back, and the gateway can deliver our own message in between. The
        text is written when the row is queued, long before any of that, so it
        is the half that closes the race. A false positive costs one dropped
        message that repeated our own sentence word for word, from our own
        account.
        """
        async with self._sessions() as session:
            return bool(
                await session.scalar(
                    select(func.count())
                    .select_from(schema.Outbound)
                    .where(
                        (schema.Outbound.sent_message_id == provider_message_id)
                        | (schema.Outbound.text == text)
                    )
                )
            )

    async def posted_by_us(self, provider_message_id: str | None) -> bool:
        """Whether this is a message this agent put there.

        By id alone, unlike `we_sent`. The question here is "did we post the
        thing they replied to", and a reply names a message id — there is no
        text to fall back on and no race to close, because by the time someone
        replies to a message the id has long since been recorded.
        """
        if not provider_message_id:
            return False
        async with self._sessions() as session:
            return bool(
                await session.scalar(
                    select(func.count())
                    .select_from(schema.Outbound)
                    .where(schema.Outbound.sent_message_id == provider_message_id)
                )
            )

    async def mark_outbound_sent(
        self, outbound_id: int, *, sent_message_id: str | None = None
    ) -> None:
        await self._set_outbound(
            outbound_id,
            state=OUTBOUND_SENT,
            sent_at=_now(),
            sent_message_id=sent_message_id,
        )

    async def record_outbound_attempt(
        self, outbound_id: int, error: str, *, retry_after: datetime | None = None
    ) -> None:
        """A failure that will be retried. Stays queued; the count is the bound."""
        async with self._sessions.begin() as session:
            await session.execute(
                update(schema.Outbound)
                .where(schema.Outbound.id == outbound_id)
                .values(
                    attempts=schema.Outbound.attempts + 1,
                    last_error=scrub(error),
                    retry_after=retry_after,
                )
            )

    async def fail_outbound(self, outbound_id: int, error: str) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(schema.Outbound)
                .where(schema.Outbound.id == outbound_id)
                .values(
                    state=OUTBOUND_FAILED,
                    attempts=schema.Outbound.attempts + 1,
                    # A provider exception can quote an Authorization header,
                    # and this is the only path by which one reaches the store.
                    last_error=scrub(error),
                )
            )

    async def mark_outbound_sent_manually(self, outbound_id: int) -> None:
        """A person delivered it after we gave up. Distinct from `failed`, so
        the trail says it was sent rather than abandoned."""
        await self._set_outbound(
            outbound_id, state=OUTBOUND_SENT_MANUALLY, sent_at=_now()
        )

    async def outbound(
        self, state: str | None = None, *, limit: int | None = None
    ) -> list[Outbound]:
        query = select(schema.Outbound)
        if state is not None:
            query = query.where(schema.Outbound.state == state)
        async with self._sessions() as session:
            rows = await session.scalars(query.order_by(schema.Outbound.id).limit(limit))
            return [_outbound(row) for row in rows]

    async def outbound_count(self, task_id: int, *, kind: str) -> int:
        """How many of one kind we have already sent about a task — which is
        what bounds asking the same question."""
        async with self._sessions() as session:
            return await session.scalar(
                select(func.count())
                .select_from(schema.Outbound)
                .where(
                    schema.Outbound.task_id == task_id,
                    schema.Outbound.kind == str(kind),
                )
            )

    async def said_since(self, kind: str, *, since) -> bool:
        """Whether a message of this kind has been queued since `since`.

        For the things the system says about itself, which belong to no task
        and so cannot be counted per task. The outbox row is the record of
        having said something — the same reasoning as `announced` — and it is
        the only record that survives a restart.
        """
        async with self._sessions() as session:
            return bool(
                await session.scalar(
                    select(func.count())
                    .select_from(schema.Outbound)
                    .where(
                        schema.Outbound.kind == str(kind),
                        schema.Outbound.created_at >= since,
                    )
                )
            )

    async def last_outbound_at(self, task_id: int, *, kind: str):
        """When we last said this kind of thing about a task."""
        async with self._sessions() as session:
            return await session.scalar(
                select(func.max(schema.Outbound.created_at)).where(
                    schema.Outbound.task_id == task_id,
                    schema.Outbound.kind == str(kind),
                )
            )

    async def has_newer_message_than(
        self, conversation: ConversationId, message_id: str
    ) -> bool:
        """Has the conversation moved on since that message?

        Numeric on the snowflake, the same way the cursor decides which message
        is newer — as text, '99' would sort after '100'.
        """
        async with self._sessions() as session:
            return await session.scalar(
                select(func.count())
                .select_from(schema.Message)
                .where(
                    schema.Message.conversation_id == str(conversation),
                    cast(schema.Message.provider_message_id, Integer)
                    > cast(literal(message_id), Integer),
                )
            ) > 0

    async def _set_outbound(self, outbound_id: int, **values) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(schema.Outbound)
                .where(schema.Outbound.id == outbound_id)
                .values(**values)
            )

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

    async def task_answered_by(self, provider_message_id: str | None) -> Task | None:
        """The open task a reply is answering, if it is answering one of ours.

        A reply names the message it responds to. When that message is one this
        system sent, the outbound row that produced it already records which
        task it was about — so which task a reply belongs to has an answer that
        is looked up rather than classified.

        `None` when the reply points at something we did not send, at an
        outbound row belonging to no task (a liveness alert, the daily
        summary), or at a task that has since finished. A reply is not a reason
        to reopen work somebody closed.
        """
        if not provider_message_id:
            return None
        async with self._sessions() as session:
            task_id = await session.scalar(
                select(schema.Outbound.task_id).where(
                    schema.Outbound.sent_message_id == provider_message_id
                )
            )
        if task_id is None:
            return None
        found = await self._tasks(
            select(schema.Task).where(
                schema.Task.id == task_id,
                schema.Task.state.in_([str(s) for s in OPEN]),
            )
        )
        return found[0] if found else None

    async def reporter_of(self, task_id: int) -> tuple[str, str] | None:
        """Who opened this task: `(author_id, author_name)` of its first message."""
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(schema.Message.author_id, schema.Message.author_name)
                    .where(schema.Message.task_id == task_id)
                    .order_by(schema.Message.created_at)
                    .limit(1)
                )
            ).first()
        return (row[0], row[1]) if row else None

    async def last_said_by_reporter(self, task_id: int) -> str | None:
        """The most recent thing the reporter said *into this task's thread* —
        a reply to a message linked to it, or to our own question about it.

        Not their latest message anywhere in the channel. That was the first
        version, and one person filing four reports in one channel produced a
        new announcement for every task each time they typed: the text changed,
        so it was "a new thing to say". What they said about something else is
        not about this. A reply names what it is about; that is the rule used
        for everything else here and it is the rule used here.
        """
        who = await self.reporter_of(task_id)
        if who is None:
            return None
        author_id, _ = who
        linked = select(schema.Message.provider_message_id).where(
            schema.Message.task_id == task_id
        )
        ours_about_it = select(schema.Outbound.sent_message_id).where(
            schema.Outbound.task_id == task_id,
            schema.Outbound.sent_message_id.is_not(None),
        )
        ours_anywhere = select(schema.Outbound.sent_message_id).where(
            schema.Outbound.sent_message_id.is_not(None)
        )
        async with self._sessions() as session:
            latest = await session.scalar(
                select(schema.Message)
                .where(
                    schema.Message.author_id == author_id,
                    # In the channel the operator tests in, the account is
                    # both sides — so "same author" alone would quote our own
                    # question back at them.
                    schema.Message.provider_message_id.not_in(ours_anywhere),
                    or_(
                        schema.Message.reply_to.in_(linked),
                        schema.Message.reply_to.in_(ours_about_it),
                    ),
                )
                .order_by(schema.Message.created_at.desc())
                .limit(1)
            )
        return latest.text if latest is not None else None

    async def has_exchanged_with(self, author_id: str) -> bool:
        """Whether the operator and this person have ever replied to each other.

        Not "have both spoken in the same channel" — the operator has spoken
        in every watched channel, which would make everybody known. A reply in
        either direction is an actual exchange, and it is the smallest thing
        that is.
        """
        m, r = schema.Message, aliased(schema.Message)
        async with self._sessions() as session:
            they_replied_to_us = (
                select(func.count())
                .select_from(m)
                .join(r, r.provider_message_id == m.reply_to)
                .where(m.author_id == author_id, r.is_own.is_(True))
            )
            we_replied_to_them = (
                select(func.count())
                .select_from(m)
                .join(r, r.provider_message_id == m.reply_to)
                .where(m.is_own.is_(True), r.author_id == author_id)
            )
            return bool(
                await session.scalar(they_replied_to_us)
                or await session.scalar(we_replied_to_them)
            )

    async def tasks_the_operator_handled(self) -> list[Task]:
        """Open tasks the operator has answered themselves.

        The operator's own messages never create work and are always stored,
        so the record is already here; this reads it. For each open task: has
        the watched account said anything in that conversation since the task
        opened that this process did not post?

        Which task a message closes follows the same rule as everything else
        about replies. A reply names what it answers — the reporter's message,
        which is linked to a task, or our own question, whose outbound row is —
        so that task closes. A message that replies to nothing closes the
        conversation's task only when there is exactly one; several open and no
        reply means guessing, and guessing here loses work.
        """
        handled: list[Task] = []
        for task in await self._tasks(
            select(schema.Task).where(schema.Task.state.in_([str(s) for s in OPEN]))
        ):
            if await self._operator_answered(task):
                handled.append(task)
        return handled

    async def _operator_answered(self, task: Task) -> bool:
        ours = select(schema.Outbound.sent_message_id).where(
            schema.Outbound.sent_message_id.is_not(None)
        )
        async with self._sessions() as session:
            said = (
                await session.execute(
                    select(schema.Message.reply_to)
                    .where(
                        schema.Message.conversation_id == str(task.conversation),
                        schema.Message.is_own.is_(True),
                        schema.Message.created_at > task.created_at,
                        schema.Message.provider_message_id.not_in(ours),
                    )
                )
            ).all()
            if not said:
                return False

            for (reply_to,) in said:
                if reply_to is None:
                    continue
                # Did they reply to the reporter, or to our own question?
                via_message = await session.scalar(
                    select(schema.Message.task_id).where(
                        schema.Message.provider_message_id == reply_to
                    )
                )
                via_ours = await session.scalar(
                    select(schema.Outbound.task_id).where(
                        schema.Outbound.sent_message_id == reply_to
                    )
                )
                if task.id in (via_message, via_ours):
                    return True

            # No reply pointing here. Theirs only if it is the one open task
            # in this conversation.
            open_here = await session.scalar(
                select(func.count())
                .select_from(schema.Task)
                .where(
                    schema.Task.conversation_id == str(task.conversation),
                    schema.Task.state.in_([str(s) for s in OPEN]),
                )
            )
            return open_here == 1 and any(r is None for (r,) in said)

    async def clear_dag_interruption(self, task_id: int) -> bool:
        """Withdraw a tool call waiting on the operator. Returns whether there
        was one.

        Only the `interruption` column: `paused_at_node` and
        `paused_question` are the record of what the run stopped on, and that
        stays true after the decision is moot. What must go is the *state a
        resume would run from*, because the tool executes the moment anyone
        approves it — so a patch left here outlives the work it belonged to
        (ticket 12).
        """
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(schema.DagState)
                .where(
                    schema.DagState.task_id == task_id,
                    schema.DagState.interruption.is_not(None),
                )
                .values(interruption=None, updated_at=_now())
            )
            return bool(result.rowcount)

    async def cancel_outbound_for(self, task_id: int) -> int:
        """Withdraw everything queued about a task. Returns how many."""
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(schema.Outbound)
                .where(
                    schema.Outbound.task_id == task_id,
                    schema.Outbound.state == OUTBOUND_QUEUED,
                )
                .values(state=OutboundState.CANCELLED)
            )
            return result.rowcount

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

    async def approve_task(self, task_id: int, *, by: str) -> None:
        """Record who approved and when. This is what the outbox joins."""
        await self._set_task(task_id, approved_at=_now(), approved_by=by)

    async def last_mention_in(self, conversation: ConversationId) -> str | None:
        """The most recent message that addressed us here — what a reply to
        this conversation should hang under."""
        async with self._sessions() as session:
            return await session.scalar(
                select(schema.Message.provider_message_id)
                .where(
                    schema.Message.conversation_id == str(conversation),
                    schema.Message.mention_type.is_not(None),
                )
                .order_by(schema.Message.created_at.desc())
                .limit(1)
            )

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

    async def dag_pauses(self, fingerprints: dict[int, str]) -> dict:
        """The node and question each of these tasks is paused on, if any.

        Keyed by task id to the fingerprint of that task's *current*
        parameters, and a pause computed against different ones is not
        returned. A pause is only cleared by a checkpoint, and a checkpoint
        only happens after a node completes — so a run that discards its state
        and then fails before finishing a node leaves the old question sitting
        there. Reporting it would ask the reporter the very thing they just
        answered, with their answer visible in the same message.

        `load_dag_state` has enforced this since the fingerprint landed. This
        is its sibling reading the same row, and one reader enforcing an
        invariant while the other ignores it is how the row starts lying.

        In bulk, because the caller has a batch and asking per task is how a
        poll that usually finds nothing costs twenty-one queries every two
        seconds.
        """
        if not fingerprints:
            return {}
        async with self._sessions() as session:
            rows = await session.execute(
                select(
                    schema.DagState.task_id,
                    schema.DagState.paused_at_node,
                    schema.DagState.paused_question,
                    schema.DagState.params_fingerprint,
                ).where(
                    schema.DagState.task_id.in_(list(fingerprints)),
                    schema.DagState.paused_at_node.is_not(None),
                )
            )
            return {
                task_id: (node, question or "")
                for task_id, node, question, fingerprint in rows
                if (fingerprint or "") == fingerprints[task_id]
            }

    async def original_text_for(self, task_id: int, limit: int = 20) -> str | None:
        """Everything the reporter has said about this task, oldest first.

        Not the messages *linked* to the task — the ones they wrote. Discord
        lets you send three messages in five seconds, and people do: a mention
        saying the API is broken, then the curl, then which environment. Only
        the first carries a mention, so only the first is in scope, and the
        rest are stored as context with no task on them. The extractor read the
        linked rows and saw one line, and the system asked for a correlationId
        the reporter had sent three seconds earlier.

        So: same conversation, same author as the message that opened the task,
        from that message onwards. Their answer to a question we asked is in
        there too, and so is the second half of their first thought.

        Excluded: anything this system posted. In a self-test the operator is
        both the reporter and the account, so "same author" would otherwise
        include our own questions.

        Bounded, because a task that stays open in a busy channel would
        otherwise grow its own prompt without limit.
        """
        async with self._sessions() as session:
            opening = (
                await session.execute(
                    select(
                        schema.Message.conversation_id,
                        schema.Message.author_id,
                        schema.Message.created_at,
                    )
                    .where(schema.Message.task_id == task_id)
                    .order_by(schema.Message.created_at)
                    .limit(1)
                )
            ).first()
            if opening is None:
                return None
            conversation_id, author_id, opened_at = opening

            ours = select(schema.Outbound.sent_message_id).where(
                schema.Outbound.sent_message_id.is_not(None)
            )
            said = await session.scalars(
                select(schema.Message.original_text)
                .where(
                    schema.Message.conversation_id == conversation_id,
                    schema.Message.author_id == author_id,
                    schema.Message.created_at >= opened_at,
                    schema.Message.provider_message_id.not_in(ours),
                )
                .order_by(schema.Message.created_at)
                .limit(limit)
            )
            return "\n".join(text for text in said if text) or None

    # ---- what the operator said about a classification -------------------

    async def record_verdict(
        self,
        *,
        provider: str,
        provider_message_id: str,
        mark: str,
        by: str,
    ) -> None:
        """Record that the operator marked this classification right or wrong.

        Upserted, so changing their mind replaces rather than accumulates.
        Marking the same thing twice leaves one row saying what they currently
        think, which is the only thing anything downstream wants to know.
        """
        statement = insert(schema.Verdict).values(
            provider=provider,
            provider_message_id=provider_message_id,
            mark=mark,
            marked_by=by,
            marked_at=_now(),
        )
        async with self._sessions.begin() as session:
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        schema.Verdict.provider,
                        schema.Verdict.provider_message_id,
                    ],
                    set_={
                        "mark": statement.excluded.mark,
                        "marked_by": statement.excluded.marked_by,
                        "marked_at": statement.excluded.marked_at,
                    },
                )
            )

    async def clear_verdict(
        self, *, provider: str, provider_message_id: str
    ) -> None:
        """The operator took the mark back. The row goes with it.

        Deleted rather than recorded as a third state, because "unmarked" and
        "never marked" mean the same thing to everything downstream: nobody
        is vouching for this one.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                delete(schema.Verdict).where(
                    schema.Verdict.provider == provider,
                    schema.Verdict.provider_message_id == provider_message_id,
                )
            )

    async def verdict_for(
        self, *, provider: str, provider_message_id: str
    ) -> tuple[str, str] | None:
        """The mark and who left it, or None if nobody has."""
        async with self._sessions() as session:
            row = await session.get(
                schema.Verdict, (provider, provider_message_id)
            )
            return (row.mark, row.marked_by) if row else None

    async def confirmed_classifications(
        self, *, limit: int = 20
    ) -> list[tuple[str, str]]:
        """Message text and the type it was marked *right* as.

        The join is the guarantee: only a classification the operator marked
        right appears here. One they never looked at is absent, and so cannot
        become an example the classifier learns its own habits from.

        Balanced across types rather than purely newest-first. Marks arrive in
        bursts — an afternoon spent confirming that a noisy channel is mostly
        `skip` is a realistic afternoon — and eight of eight examples reading
        "this one is skip" teaches the classifier to skip. Recency still
        orders *within* a type; what is shared out is the eight slots.
        """
        async with self._sessions() as session:
            rows = await session.execute(
                select(schema.Message.text, schema.Message.decision_type)
                .join(
                    schema.Verdict,
                    (schema.Verdict.provider == schema.Message.provider)
                    & (
                        schema.Verdict.provider_message_id
                        == schema.Message.provider_message_id
                    ),
                )
                .where(
                    schema.Verdict.mark == "right",
                    # A closed set, not merely "not null". `mark_triaged`
                    # also writes the *state* a message ended in — a
                    # low-confidence one is recorded as `needs_human` — and
                    # marking that right is a perfectly sensible thing for
                    # the operator to do. Showing it back as an example
                    # would teach the classifier a label it has no tool for.
                    schema.Message.decision_type.in_(CLASSIFIABLE),
                )
                .order_by(schema.Verdict.marked_at.desc())
                # Deeper than `limit`, because the balancing below picks from
                # this rather than taking it whole.
                .limit(max(limit * 4, limit))
            )
            return _balanced([(text, kind) for text, kind in rows], limit)

    # ---- workflow graph state -------------------------------------------

    async def save_dag_state(
        self,
        task_id: int,
        *,
        dag_name: str,
        results: dict,
        #: The path that produced those results. Saved with them because they
        #: are read together and are only meaningful together.
        trail: list[str] | None = None,
        #: Required, not defaulted. `_fingerprint` never returns "" — even for
        #: no parameters at all — so an empty one is only what a caller who
        #: forgot this argument writes, and writing it guarantees the next
        #: load throws the state away.
        params_fingerprint: str,
        paused_at_node: str | None = None,
        paused_question: str | None = None,
        #: The SDK's own run state, set only while a `needs_approval` tool
        #: call inside `paused_at_node` is waiting on the operator (ticket
        #: 07). `None` clears it — every ordinary checkpoint passes nothing,
        #: which is what makes approving (or a fresh pass discarding stale
        #: state) the only two ways it survives past the write that set it.
        interruption: dict | None = None,
    ) -> None:
        """Record what a task's workflow graph has produced so far.

        Upserted on `task_id`: one row per task, rewritten after every node.
        This is what makes a restart resume rather than start over, so it is
        written before the next node begins rather than at the end of the run.
        """
        statement = insert(schema.DagState).values(
            task_id=task_id,
            dag_name=dag_name,
            params_fingerprint=params_fingerprint,
            results=results,
            trail=list(trail or []),
            paused_at_node=paused_at_node,
            paused_question=paused_question,
            interruption=interruption,
            updated_at=_now(),
        )
        async with self._sessions.begin() as session:
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[schema.DagState.task_id],
                    set_={
                        "dag_name": statement.excluded.dag_name,
                        "params_fingerprint": (
                            statement.excluded.params_fingerprint
                        ),
                        "results": statement.excluded.results,
                    "trail": statement.excluded.trail,
                        "paused_at_node": statement.excluded.paused_at_node,
                        "paused_question": statement.excluded.paused_question,
                        "interruption": statement.excluded.interruption,
                        "updated_at": statement.excluded.updated_at,
                    },
                )
            )

    async def load_dag_state(
        self,
        task_id: int,
        *,
        dag_name: str | None = None,
        params_fingerprint: str | None = None,
    ) -> dict | None:
        """What the graph recorded, or None if it has not run.

        `dag_name` is the graph asking. When it does not match the one that
        wrote the row, nothing is returned and the graph starts over: node
        names are only meaningful inside the graph that defined them, and
        handing a renamed or rewritten graph its predecessor's results makes
        it skip nodes on the strength of work that was never done. A restart
        costs a few tool calls; a reply composed from another graph's
        findings is wrong in a way nobody can see.

        `params_fingerprint` is the same argument about the *inputs*. A node
        concluded what it concluded from the parameters it was given, and the
        whole point of asking the reporter for a correlationId is that the
        answer changes. State written before they answered says "there was
        nothing to look up", which is true of the old parameters and false of
        the new ones — and believing it means the graph asks a question,
        receives an answer, and then reads not one log line.

        Returns the raw results mapping; rebuilding it into a `DAGState` is
        the caller's business, so this module keeps knowing nothing about the
        graph. `load_dag_progress` returns the path alongside it, for the
        caller that resumes rather than only reads.
        """
        row = await self._dag_row(task_id, dag_name, params_fingerprint)
        return None if row is None else dict(row.results or {})

    async def load_dag_progress(
        self,
        task_id: int,
        *,
        dag_name: str | None = None,
        params_fingerprint: str | None = None,
    ) -> tuple[dict, list[str]] | None:
        """What the graph recorded, and the path that recorded it.

        One read for both, because they are only meaningful together and a
        resumed run needs both to answer "what did this graph decide?" — the
        results say which nodes have run, the path says in what order, and
        `Pool._outcome` reads the second backwards.
        """
        row = await self._dag_row(task_id, dag_name, params_fingerprint)
        if row is None:
            return None
        return dict(row.results or {}), list(row.trail or [])

    async def _dag_row(
        self, task_id: int, dag_name: str | None, params_fingerprint: str | None
    ):
        """The row, once it has passed both tests of whether it is still
        anybody's to read. One place, so the two readers cannot disagree about
        when state is stale."""
        async with self._sessions() as session:
            row = await session.get(schema.DagState, task_id)
            if row is None:
                return None
            if dag_name is not None and row.dag_name != dag_name:
                log.info(
                    "task %d: discarding state from %r, this is %r",
                    task_id,
                    row.dag_name,
                    dag_name,
                )
                return None
            if (
                params_fingerprint is not None
                and (row.params_fingerprint or "") != params_fingerprint
            ):
                log.info(
                    "task %d: parameters are %s, the graph concluded against "
                    "%s — discarding what it concluded",
                    task_id,
                    params_fingerprint,
                    row.params_fingerprint or "(none recorded)",
                )
                return None
            return row

    async def dag_pause(self, task_id: int) -> tuple[str, str] | None:
        """The node that paused and the question it asked, if any."""
        async with self._sessions() as session:
            row = await session.get(schema.DagState, task_id)
            if row is None or not row.paused_at_node:
                return None
            return row.paused_at_node, row.paused_question or ""

    async def dag_interruption(self, task_id: int) -> dict | None:
        """Everything `decide_pending_action` needs to resume or decline a
        paused tool call — `None` unless one is actually waiting.

        Read raw rather than through `load_dag_state`, which filters by a
        fingerprint the caller does not have yet at this point: whether the
        stored state is still good for the task's *current* parameters is
        exactly what resuming has to check, not something to discard before
        the check runs.
        """
        async with self._sessions() as session:
            row = await session.get(schema.DagState, task_id)
            if row is None or row.interruption is None:
                return None
            return {
                "dag_name": row.dag_name,
                "results": dict(row.results or {}),
                "trail": list(row.trail or []),
                "params_fingerprint": row.params_fingerprint or "",
                "paused_at_node": row.paused_at_node,
                "interruption": row.interruption,
            }

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
        """One task, freshest read — `decide_pending_action` needs current
        params, not the ones a stored graph state was checkpointed against."""
        async with self._sessions() as session:
            row = await session.get(schema.Task, task_id)
            return _task(row) if row is not None else None

    async def _tasks(self, query) -> list[Task]:
        async with self._sessions() as session:
            return [_task(row) for row in await session.scalars(query)]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _memory_id() -> str:
    """Opaque and sparse, not sequential.

    `secrets.token_hex` rather than the row's own autoincrement: a model that
    invents an id has to land on a string nobody would guess, not merely one a
    counter has not reached yet. Collisions are not handled with a retry loop
    — at this length, over the row counts one channel's memory will ever
    reach, the birthday bound on a collision is astronomically below the
    chance of the process crashing first, and a caller that somehow hit one
    would get an ordinary primary-key violation, not silent corruption.
    """
    return secrets.token_hex(6)


def _memory(row: schema.Memory) -> Memory:
    return Memory(
        id=row.id,
        channel_id=row.channel_id,
        agent=row.agent,
        text=row.text,
        task_id=row.task_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
        deleted_by=row.deleted_by,
    )


def _model_call(row: schema.ModelCall) -> ModelCall:
    """One row, as the domain sees it.

    Written out once rather than three times: the three readers of this table
    each built the dataclass by hand, so a column added to `schema` reached
    whichever of them somebody remembered.
    """
    return ModelCall(
        agent=row.agent,
        model=row.model,
        system_prompt=row.system_prompt,
        prompt=row.prompt,
        output=row.output,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        message_id=row.message_id,
        task_id=row.task_id,
        node=row.node,
        latency_ms=row.latency_ms,
        attempt=row.attempt or 1,
        created_at=row.created_at,
    )


def _tool_call(row: schema.ToolCall) -> ToolCall:
    """One row, as the domain sees it — for the reason `_model_call` gives.

    It was built by hand inside `tools_for_tasks`, which was the table's only
    reader, so it read as a local detail rather than a missing converter. It
    stopped being one the moment a second reader existed (`flow_for`), which
    is exactly the shape `_model_call`'s own docstring warns about.
    """
    return ToolCall(
        agent=row.agent,
        tool=row.tool,
        arguments=row.arguments,
        result=row.result,
        failed=bool(row.failed),
        latency_ms=row.latency_ms,
        message_id=row.message_id,
        task_id=row.task_id,
        node=row.node,
        created_at=row.created_at,
    )


def _event(row: schema.Message) -> InboundEvent:
    return InboundEvent(
        provider=row.provider,
        provider_message_id=row.provider_message_id,
        channel_id=row.channel_id,
        thread_id=row.thread_id,
        author_id=row.author_id,
        author_name=row.author_name,
        text=row.text,
        created_at=row.created_at,
        mention_type=MentionType(row.mention_type) if row.mention_type else None,
        is_own=row.is_own,
        reply_to=row.reply_to,
    )


def _outbound(row: schema.Outbound) -> Outbound:
    return Outbound(
        id=row.id,
        task_id=row.task_id,
        conversation=ConversationId.parse(row.conversation_id),
        kind=row.kind,
        sender=row.sender,
        text=row.text,
        reply_to=row.reply_to,
        state=row.state,
        attempts=row.attempts,
        last_error=row.last_error,
    )


def _task(row: schema.Task) -> Task:
    return Task(
        id=row.id,
        conversation=ConversationId.parse(row.conversation_id),
        type=row.type,
        state=row.state,
        confidence=row.confidence,
        params=row.params or {},
        created_at=row.created_at,
    )


def _balanced(rows: list[tuple[str, str]], limit: int) -> list[tuple[str, str]]:
    """Share the example slots out across the types, newest first within each.

    Round-robin over the types present, taking the newest unused example of
    each in turn. A type nobody has confirmed simply is not in the rotation —
    this balances what exists rather than inventing what does not.

    Order is not preserved overall, and does not need to be: these go into the
    prompt as a set of labelled examples, not as a transcript.
    """
    by_type: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        by_type.setdefault(row[1], []).append(row)

    taken: list[tuple[str, str]] = []
    while len(taken) < limit and any(by_type.values()):
        for remaining in by_type.values():
            if not remaining:
                continue
            taken.append(remaining.pop(0))
            if len(taken) == limit:
                break
    return taken
