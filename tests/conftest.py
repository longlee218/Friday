from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from friday.config import IngestConfig
from friday.db import Database
from friday.inbox import Inbox
from friday.models import InboundEvent, MentionType


class FakeProvider:
    """Stands in for a chat platform at the Provider seam.

    Tests queue already-normalised events; the platform-specific work of turning
    a raw payload into an InboundEvent belongs to a real provider and is tested
    against that provider, not here.
    """

    name = "fake"

    def __init__(self) -> None:
        self._queued: list[InboundEvent] = []
        self._history: dict[str, list[InboundEvent]] = {}
        self.history_calls: list[tuple[str, str | None]] = []
        self._recent: dict[str, list[InboundEvent]] = {}
        self.recent_calls: list[tuple[str, str, int]] = []
        self.reconnected = asyncio.Event()
        # By default the live stream ends once queued events are drained, so
        # tests terminate. Set True when a test needs it to stay open.
        self.keep_open = False
        self._closed = asyncio.Event()

    def close(self) -> None:
        self._closed.set()

    def emit(self, event: InboundEvent) -> None:
        """Deliver on the live path."""
        self._queued.append(event)

    def emit_recent(self, conversation_id: str, *events: InboundEvent) -> None:
        """Make events retrievable as prior context for a conversation."""
        self._recent.setdefault(conversation_id, []).extend(events)

    def emit_history(self, channel_id: str, *events: InboundEvent) -> None:
        """Make events retrievable by a sweep of this channel."""
        self._history.setdefault(channel_id, []).extend(events)

    async def stream(self):
        for event in self._queued:
            yield event
        if self.keep_open:
            await self._closed.wait()

    async def recent(self, conversation_id: str, *, before: str, limit: int):
        self.recent_calls.append((conversation_id, before, limit))
        for event in self._recent.get(conversation_id, [])[-limit:]:
            yield event

    async def history(self, channel_id: str, *, after: str | None):
        self.history_calls.append((channel_id, after))
        for event in self._history.get(channel_id, []):
            if after is None or int(event.provider_message_id) > int(after):
                yield event


def make_event(
    *,
    message_id: str = "m1",
    channel_id: str = "watched",
    thread_id: str | None = None,
    mention_type: MentionType | None = MentionType.DIRECT,
    text: str = "hey can you look at this",
    author_id: str = "u-reporter",
    author_name: str = "reporter",
) -> InboundEvent:
    return InboundEvent(
        provider="fake",
        provider_message_id=message_id,
        channel_id=channel_id,
        thread_id=thread_id,
        author_id=author_id,
        author_name=author_name,
        text=text,
        created_at=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
        mention_type=mention_type,
    )


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
async def db():
    database = await Database.connect(":memory:")
    yield database
    await database.close()


@pytest.fixture
def config() -> IngestConfig:
    return IngestConfig(
        watched_channels=frozenset({"watched"}),
        mention_types=frozenset(
            {MentionType.DIRECT, MentionType.ROLE, MentionType.DM}
        ),
    )


@pytest.fixture
def inbox(provider, db, config) -> Inbox:
    return Inbox(provider=provider, db=db, config=config)


async def captured(inbox: Inbox) -> list[InboundEvent]:
    return [event async for event in inbox.stream()]
