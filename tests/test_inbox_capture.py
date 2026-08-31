"""Ticket 01 — capture a live mention, driven through the Provider seam."""

from __future__ import annotations

from datetime import datetime, timezone

from conftest import captured, make_event
from friday.config import IngestConfig
from friday.inbox import Inbox
from friday.conversation import ConversationId
from friday.models import MentionType


async def test_direct_mention_in_watched_channel_is_captured(inbox, provider):
    provider.emit(make_event(message_id="m1"))

    events = await captured(inbox)

    assert [e.provider_message_id for e in events] == ["m1"]


async def test_captured_event_is_stored_with_its_details(inbox, provider, db):
    provider.emit(
        make_event(message_id="m1", text="checkout is 500ing", author_name="dana")
    )

    await captured(inbox)

    stored = await db.mentions()
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
    assert len(await db.mentions()) == 1


async def test_message_in_unwatched_channel_is_ignored(inbox, provider, db):
    provider.emit(make_event(message_id="m1", channel_id="some-other-channel"))

    assert await captured(inbox) == []
    assert await db.mentions() == []


async def test_message_that_does_not_address_the_account_is_ignored(
    inbox, provider, db
):
    provider.emit(make_event(message_id="m1", mention_type=None))

    assert await captured(inbox) == []
    assert await db.mentions() == []


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

    assert await db.conversations() == [
        ConversationId("fake", "watched", "t1")
    ]


async def test_two_events_in_one_conversation_share_a_session(inbox, provider, db):
    provider.emit(make_event(message_id="m1", thread_id="t1"))
    provider.emit(make_event(message_id="m2", thread_id="t1"))

    await captured(inbox)

    assert len(await db.conversations()) == 1


async def test_threads_and_their_parent_channel_are_separate_sessions(
    inbox, provider, db
):
    provider.emit(make_event(message_id="m1", thread_id=None))
    provider.emit(make_event(message_id="m2", thread_id="t1"))

    await captured(inbox)

    assert sorted(s.thread_id or "" for s in await db.conversations()) == ["", "t1"]


async def test_an_unwatched_channel_is_reported_as_the_reason_for_dropping(
    inbox, provider, caplog
):
    """Silent drops are the hardest failure to debug; the reason must be logged."""
    import logging

    provider.emit(make_event(message_id="m1", channel_id="elsewhere"))

    with caplog.at_level(logging.DEBUG, logger="friday.inbox"):
        await captured(inbox)

    assert "channel elsewhere is not watched" in caplog.text


async def test_seeing_a_message_advances_that_channels_cursor(inbox, provider, db):
    provider.emit(make_event(message_id="100"))

    await captured(inbox)

    assert await db.cursor_for("fake", "watched") == "100"


async def test_the_cursor_tracks_the_newest_message_seen(inbox, provider, db):
    provider.emit(make_event(message_id="100"))
    provider.emit(make_event(message_id="200"))

    await captured(inbox)

    assert await db.cursor_for("fake", "watched") == "200"


async def test_an_older_message_arriving_later_does_not_rewind_the_cursor(
    inbox, provider, db
):
    """The sweep replays old messages after newer live ones. Rewinding the
    cursor would make it re-fetch the same window forever."""
    provider.emit(make_event(message_id="200"))
    provider.emit(make_event(message_id="100"))

    await captured(inbox)

    assert await db.cursor_for("fake", "watched") == "200"


async def test_the_cursor_is_compared_by_age_not_alphabetically(
    inbox, provider, db
):
    """Message ids are numeric snowflakes: '99' is older than '100', but sorts
    after it as text."""
    provider.emit(make_event(message_id="100"))
    provider.emit(make_event(message_id="99"))

    await captured(inbox)

    assert await db.cursor_for("fake", "watched") == "100"


async def test_a_message_that_is_dropped_still_advances_the_cursor(
    inbox, provider, db
):
    """Otherwise the sweep re-fetches traffic we have already looked at."""
    provider.emit(make_event(message_id="100", mention_type=None))

    await captured(inbox)

    assert await db.cursor_for("fake", "watched") == "100"


async def test_an_unknown_channel_has_no_cursor(db):
    assert await db.cursor_for("fake", "never-seen") is None


async def test_cursors_survive_a_restart(tmp_path, provider, config):
    from friday.db import Database
    from friday.inbox import Inbox

    path = str(tmp_path / "friday.db")
    first = await Database.connect(path, create=True)
    provider.emit(make_event(message_id="100"))
    await captured(Inbox(provider=provider, db=first, config=config))
    await first.close()

    reopened = await Database.connect(path, create=True)
    try:
        assert await reopened.cursor_for("fake", "watched") == "100"
    finally:
        await reopened.close()
