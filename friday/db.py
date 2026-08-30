from __future__ import annotations

from datetime import datetime

import aiosqlite

from friday.models import InboundEvent, MentionType, Session

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    provider            TEXT NOT NULL,
    provider_message_id TEXT NOT NULL,
    channel_id          TEXT NOT NULL,
    thread_id           TEXT,
    author_id           TEXT NOT NULL,
    author_name         TEXT NOT NULL,
    text                TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    mention_type        TEXT,
    PRIMARY KEY (provider, provider_message_id)
);

-- thread_id is stored as '' rather than NULL for the absent case: SQLite treats
-- NULLs as distinct in a key, which would let one channel accumulate a new
-- session per message.
CREATE TABLE IF NOT EXISTS sessions (
    provider   TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    thread_id  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (provider, channel_id, thread_id)
);
"""


class Database:
    """Async access to the single store. Never call this from a sync path:
    a blocking database call on the event loop stalls ingestion."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    @classmethod
    async def connect(cls, path: str) -> "Database":
        connection = await aiosqlite.connect(path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.executescript(SCHEMA)
        await connection.commit()
        return cls(connection)

    async def close(self) -> None:
        await self._connection.close()

    async def record_event(self, event: InboundEvent) -> bool:
        """Store an event. Returns False if this message was already recorded.

        The primary key is (provider, provider_message_id), which is what makes
        the two delivery paths safe to run concurrently.
        """
        cursor = await self._connection.execute(
            """
            INSERT OR IGNORE INTO events (
                provider, provider_message_id, channel_id, thread_id,
                author_id, author_name, text, created_at, mention_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.provider,
                event.provider_message_id,
                event.channel_id,
                event.thread_id,
                event.author_id,
                event.author_name,
                event.text,
                event.created_at.isoformat(),
                event.mention_type.value if event.mention_type else None,
            ),
        )
        await self._connection.commit()
        return cursor.rowcount == 1

    async def record_session(self, event: InboundEvent) -> None:
        """Ensure the conversation this event belongs to exists."""
        await self._connection.execute(
            """
            INSERT OR IGNORE INTO sessions (provider, channel_id, thread_id)
            VALUES (?, ?, ?)
            """,
            (event.provider, event.channel_id, event.thread_id or ""),
        )
        await self._connection.commit()

    async def sessions(self) -> list[Session]:
        cursor = await self._connection.execute(
            "SELECT * FROM sessions ORDER BY channel_id, thread_id"
        )
        return [
            Session(
                provider=row["provider"],
                channel_id=row["channel_id"],
                thread_id=row["thread_id"] or None,
            )
            for row in await cursor.fetchall()
        ]

    async def events(self) -> list[InboundEvent]:
        cursor = await self._connection.execute(
            "SELECT * FROM events ORDER BY created_at, provider_message_id"
        )
        return [_row_to_event(row) for row in await cursor.fetchall()]


def _row_to_event(row: aiosqlite.Row) -> InboundEvent:
    return InboundEvent(
        provider=row["provider"],
        provider_message_id=row["provider_message_id"],
        channel_id=row["channel_id"],
        thread_id=row["thread_id"],
        author_id=row["author_id"],
        author_name=row["author_name"],
        text=row["text"],
        created_at=datetime.fromisoformat(row["created_at"]),
        mention_type=(
            MentionType(row["mention_type"]) if row["mention_type"] else None
        ),
    )
