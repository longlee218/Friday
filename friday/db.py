"""The only store.

A deep module: every caller sees domain dataclasses, and nothing above this
seam knows SQLAlchemy exists. Mapped classes live in `friday.schema` and are
converted at the edge, so `friday.models` stays free of persistence concerns.

Never call this from a sync path — a blocking database call on the event loop
stalls ingestion.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Integer, cast, event, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from friday import schema
from friday.conversation import ConversationId
from friday.models import InboundEvent, MentionType, Task

__all__ = ["Database"]


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
        self, conversation: ConversationId | None = None
    ) -> list[InboundEvent]:
        """Everything seen, or everything seen in one conversation."""
        query = select(schema.Message)
        if conversation is not None:
            query = query.where(schema.Message.conversation_id == str(conversation))
        return await self._events(
            query.order_by(
                schema.Message.created_at, schema.Message.provider_message_id
            )
        )

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
                    schema.Task.state != "done",
                )
                .order_by(schema.Task.id.desc())
                .limit(1)
            )
            return _task(row) if row else None

    async def set_task_state(self, task_id: int, state: str) -> None:
        await self._set_task(task_id, state=state)

    async def set_task_params(self, task_id: int, params: dict) -> None:
        await self._set_task(task_id, params=params)

    async def _set_task(self, task_id: int, **values) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(schema.Task).where(schema.Task.id == task_id).values(**values)
            )

    async def tasks_in_state(self, state: str, limit: int = 20) -> list[Task]:
        return await self._tasks(
            select(schema.Task)
            .where(schema.Task.state == state)
            .order_by(schema.Task.id)
            .limit(limit)
        )

    async def tasks(self) -> list[Task]:
        return await self._tasks(select(schema.Task).order_by(schema.Task.id))

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
