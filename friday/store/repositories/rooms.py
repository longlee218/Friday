"""The rooms repository — a `Database` mixin (ticket 16)."""

from __future__ import annotations

from friday.store._common import *  # noqa: F401,F403 (shared store internals)


class RoomsRepo:

    async def rooms(self) -> list[dict]:
        """Every conversation this system has seen, for the left-hand list.

        One query set rather than one request per room: a sidebar that had to
        fetch each room's messages to say how many there are is a sidebar
        that gets slower the more work you have.

        `name` is the operator's own label and `None` when they have not
        given one — which is not the same as `""`, and is why the writer
        turns an empty string back into `None` rather than storing a name
        that renders as nothing.

        **Driven from `messages`, with the name joined on.** It joined
        `conversations` first, which only `record_conversation` writes — so a
        room whose row had never been written disappeared from this list
        while its messages sat plainly in the table. That is the same shape
        as a dropped mention: a room that is not listed is indistinguishable
        from a room that never existed. What makes a room real here is that
        somebody said something in it; the name is decoration on top.
        """
        counted = (
            select(
                schema.Message.conversation_id,
                func.count().label("messages"),
                func.max(schema.Message.created_at).label("last_at"),
                func.min(schema.Message.channel_id).label("channel_id"),
            )
            .group_by(schema.Message.conversation_id)
            .subquery()
        )
        query = (
            select(
                counted.c.conversation_id,
                schema.Conversation.name,
                counted.c.messages,
                counted.c.last_at,
                counted.c.channel_id,
            )
            .outerjoin(
                schema.Conversation,
                schema.Conversation.id == counted.c.conversation_id,
            )
            .order_by(counted.c.last_at.desc())
        )
        async with self._sessions() as session:
            return [
                {
                    "id": row.conversation_id,
                    "name": row.name,
                    "channel_id": row.channel_id,
                    "messages": row.messages,
                    "last_at": row.last_at,
                }
                for row in await session.execute(query)
            ]

    async def name_conversation(self, conversation_id: str, name: str) -> bool:
        """Give a room a name, or take its name back. `False` if no such room.

        An empty name is stored as `NULL`, not as `""`: "unnamed" is a state
        the list already renders, and a second spelling of it would mean two
        ways to be nameless and a bug the day one of them is missed.

        Creates the row when the room has messages but no `conversations`
        entry — the same gap `rooms` was hiding. A room the operator can see
        and cannot label would be a worse answer than either.
        """
        async with self._sessions.begin() as session:
            spoke = await session.scalar(
                select(func.count())
                .select_from(schema.Message)
                .where(schema.Message.conversation_id == conversation_id)
            )
            if not spoke:
                return False
            row = await session.get(schema.Conversation, conversation_id)
            if row is None:
                row = schema.Conversation(id=conversation_id)
                session.add(row)
            row.name = name.strip() or None
            return True

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
