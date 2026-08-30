"""Ticket 01 — capture a live mention, driven through the Provider seam."""

from __future__ import annotations

from datetime import datetime, timezone

from conftest import captured, make_event
from friday.config import IngestConfig
from friday.inbox import Inbox
from friday.models import MentionType, Session


async def test_direct_mention_in_watched_channel_is_captured(inbox, provider):
    provider.emit(make_event(message_id="m1"))

    events = await captured(inbox)

    assert [e.provider_message_id for e in events] == ["m1"]


async def test_captured_event_is_stored_with_its_details(inbox, provider, db):
    provider.emit(
        make_event(message_id="m1", text="checkout is 500ing", author_name="dana")
    )

    await captured(inbox)

    stored = await db.events()
    assert len(stored) == 1
    assert stored[0].provider_message_id == "m1"
    assert stored[0].text == "checkout is 500ing"
    assert stored[0].author_name == "dana"
    assert stored[0].mention_type is MentionType.DIRECT
    assert stored[0].created_at == datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


async def test_same_message_delivered_twice_is_captured_once(inbox, provider, db):
    """Two delivery paths will feed this pipeline. Both must be safe to run."""
    provider.emit(make_event(message_id="m1"))
    provider.emit(make_event(message_id="m1"))

    events = await captured(inbox)

    assert [e.provider_message_id for e in events] == ["m1"]
    assert len(await db.events()) == 1


async def test_message_in_unwatched_channel_is_ignored(inbox, provider, db):
    provider.emit(make_event(message_id="m1", channel_id="some-other-channel"))

    assert await captured(inbox) == []
    assert await db.events() == []


async def test_message_that_does_not_address_the_account_is_ignored(
    inbox, provider, db
):
    provider.emit(make_event(message_id="m1", mention_type=None))

    assert await captured(inbox) == []
    assert await db.events() == []


async def test_role_mention_is_captured(inbox, provider):
    provider.emit(make_event(message_id="m1", mention_type=MentionType.ROLE))

    events = await captured(inbox)

    assert [e.mention_type for e in events] == [MentionType.ROLE]


async def test_direct_message_is_captured_despite_not_being_a_watched_channel(
    inbox, provider
):
    """DMs are scoped by being DMs, not by the channel whitelist."""
    provider.emit(
        make_event(message_id="m1", channel_id="dm-1", mention_type=MentionType.DM)
    )

    events = await captured(inbox)

    assert [e.mention_type for e in events] == [MentionType.DM]


async def test_mention_type_switched_off_in_config_is_ignored(provider, db):
    only_direct = IngestConfig(
        watched_channels=frozenset({"watched"}),
        mention_types=frozenset({MentionType.DIRECT}),
    )
    inbox = Inbox(provider=provider, db=db, config=only_direct)
    provider.emit(make_event(message_id="m1", mention_type=MentionType.ROLE))

    assert await captured(inbox) == []


async def test_capturing_an_event_records_its_conversation(inbox, provider, db):
    provider.emit(make_event(message_id="m1", channel_id="watched", thread_id="t1"))

    await captured(inbox)

    assert await db.sessions() == [
        Session(provider="fake", channel_id="watched", thread_id="t1")
    ]


async def test_two_events_in_one_conversation_share_a_session(inbox, provider, db):
    provider.emit(make_event(message_id="m1", thread_id="t1"))
    provider.emit(make_event(message_id="m2", thread_id="t1"))

    await captured(inbox)

    assert len(await db.sessions()) == 1


async def test_threads_and_their_parent_channel_are_separate_sessions(
    inbox, provider, db
):
    provider.emit(make_event(message_id="m1", thread_id=None))
    provider.emit(make_event(message_id="m2", thread_id="t1"))

    await captured(inbox)

    assert sorted(s.thread_id or "" for s in await db.sessions()) == ["", "t1"]
