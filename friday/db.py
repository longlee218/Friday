"""The only store.

A deep module: every caller sees domain dataclasses, and nothing above this
seam knows SQLAlchemy exists. Mapped classes live in `friday.schema` and are
converted at the edge, so `friday.models` stays free of persistence concerns.

Never call this from a sync path — a blocking database call on the event loop
stalls ingestion.
"""

from __future__ import annotations

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
    tuple_,
    update,
)
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from friday import schema
from friday.conversation import ConversationId
from friday.llm_log import ModelCall
from friday.models import (
    InboundEvent,
    MentionType,
    Note,
    Observation,
    Outbound,
    Task,
)
from friday.redact import scrub
from friday.tasks import OPEN, IllegalTransition, TaskState, may_move

__all__ = ["Database"]

_OLDEST_FIRST = (schema.Message.created_at, schema.Message.provider_message_id)
#: Ties break on the id cast as a number — two messages can share a timestamp,
#: and a snowflake is the platform's own answer to which came first.
_NEWEST_FIRST = (
    schema.Message.created_at.desc(),
    cast(schema.Message.provider_message_id, Integer).desc(),
)

OUTBOUND_QUEUED = "queued"
OUTBOUND_SENT = "sent"
OUTBOUND_FAILED = "failed"
OUTBOUND_SENT_MANUALLY = "sent_manually"

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
            created_at=event.created_at,
            is_own=event.is_own,
            mention_type=event.mention_type.value if event.mention_type else None,
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

    async def untriaged_mentions(self, limit: int = 50) -> list[InboundEvent]:
        """The queue: mentions nobody has looked at, oldest first.

        Context shares the table but is never classified — the queue is a
        `WHERE` clause, not a second table.
        """
        return await self._events(
            select(schema.Message)
            .where(
                schema.Message.mention_type.is_not(None),
                schema.Message.triaged_at.is_(None),
            )
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

    # ---- model calls ---------------------------------------------------

    async def record_model_call(self, **values) -> None:
        values.setdefault("created_at", _now())
        async with self._sessions.begin() as session:
            session.add(schema.ModelCall(**values))

    async def model_calls(
        self, *, message_id: str | None = None, limit: int = 50
    ) -> list[ModelCall]:
        query = select(schema.ModelCall)
        if message_id is not None:
            query = query.where(schema.ModelCall.message_id == message_id)
        async with self._sessions() as session:
            rows = await session.scalars(
                query.order_by(schema.ModelCall.created_at.desc(),
                               schema.ModelCall.id.desc()).limit(limit)
            )
            return [
                ModelCall(
                    agent=row.agent,
                    model=row.model,
                    system_prompt=row.system_prompt,
                    prompt=row.prompt,
                    output=row.output,
                    input_tokens=row.input_tokens,
                    output_tokens=row.output_tokens,
                    message_id=row.message_id,
                    created_at=row.created_at,
                )
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

    # ---- observations ---------------------------------------------------

    async def record_observation(
        self, *, task_id: int, category: str, text: str
    ) -> None:
        async with self._sessions.begin() as session:
            session.add(
                schema.Observation(
                    task_id=task_id, category=category, text=text, created_at=_now()
                )
            )

    async def observations(
        self, *, task_id: int | None = None, limit: int = 100
    ) -> list[Observation]:
        query = select(schema.Observation)
        if task_id is not None:
            query = query.where(schema.Observation.task_id == task_id)
        async with self._sessions() as session:
            rows = await session.scalars(
                query.order_by(schema.Observation.id).limit(limit)
            )
            return [
                Observation(
                    id=row.id,
                    task_id=row.task_id,
                    category=row.category,
                    text=row.text,
                    created_at=row.created_at,
                    promoted_at=row.promoted_at,
                )
                for row in rows
            ]

    async def clear_observations(self, ids: list[int]) -> None:
        """Considered is considered, promoted or not."""
        async with self._sessions.begin() as session:
            await session.execute(
                delete(schema.Observation).where(schema.Observation.id.in_(ids))
            )

    # ---- notes ----------------------------------------------------------

    async def approved_task_ids(self) -> set[int]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Task.id).where(schema.Task.approved_at.is_not(None))
            )
            return set(rows)

    async def support_note(self, *, category: str, text: str, by: int) -> int:
        """Add support for a note, creating it if this is the first. Returns
        the total, which is what decides whether it is believed yet."""
        statement = insert(schema.Note).values(
            category=category, text=text, support=by, created_at=_now()
        )
        async with self._sessions.begin() as session:
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[schema.Note.category, schema.Note.text],
                    set_={"support": schema.Note.support + by},
                )
            )
            return await session.scalar(
                select(schema.Note.support).where(
                    schema.Note.category == category, schema.Note.text == text
                )
            )

    async def notes(self, *, limit: int = 100) -> list[Note]:
        """Every note row, in a stable order — best supported first, then by
        text, so two renders between promotions are byte-identical.

        Includes rows still below their category's threshold. Those are the
        accumulated evidence: observations are used up when they are considered,
        so this is the only place a first sighting can wait for a second.
        Deciding which of them are *believed* needs the thresholds, and those
        belong to `friday.notes`, not here.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Note)
                .order_by(schema.Note.support.desc(), schema.Note.text)
                .limit(limit)
            )
            return [
                Note(
                    category=row.category,
                    text=row.text,
                    support=row.support,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    async def trim_notes(self, *, keep: int) -> None:
        """A prompt has room for a handful of these. The best supported stay."""
        async with self._sessions.begin() as session:
            keeping = (
                select(schema.Note.category, schema.Note.text)
                .order_by(schema.Note.support.desc(), schema.Note.text)
                .limit(keep)
                .subquery()
            )
            await session.execute(
                delete(schema.Note).where(
                    tuple_(schema.Note.category, schema.Note.text).not_in(
                        select(keeping.c.category, keeping.c.text)
                    )
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

    async def tasks_needing_announcement(
        self, kind: str, *, state: str, limit: int = 20
    ) -> list[Task]:
        """Tasks in a state that nobody has been told about.

        One anti-join rather than a count per task: the outbox row is already
        the record of having said something, so asking it directly beats
        denormalising the same fact onto the task and keeping the two in step.
        """
        told = select(schema.Outbound.task_id).where(
            schema.Outbound.task_id == schema.Task.id,
            schema.Outbound.kind == str(kind),
        )
        return await self._tasks(
            select(schema.Task)
            .where(schema.Task.state == str(state), ~told.exists())
            .order_by(schema.Task.id)
            .limit(limit)
        )

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

    async def _tasks(self, query) -> list[Task]:
        async with self._sessions() as session:
            return [_task(row) for row in await session.scalars(query)]


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
