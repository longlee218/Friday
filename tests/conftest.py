from __future__ import annotations

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

    def emit(self, event: InboundEvent) -> None:
        self._queued.append(event)

    async def stream(self):
        for event in self._queued:
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
