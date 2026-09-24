"""Ticket 01 — capture a live mention, driven through the Provider seam."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from conftest import captured, make_event
from friday.config import IngestConfig
from friday.inbox import Inbox
from friday.domain.conversation import ConversationId
from friday.domain.models import MentionType


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
    from friday.store.db import Database
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


async def test_a_message_is_recorded_before_the_cursor_moves_past_it(inbox, provider, db):
    """The cursor says "read up to here", and the sweep asks for what comes
    after. Moving it first means a crash between the two loses the message for
    good — nothing will ever look at that range again."""
    provider.emit(make_event(message_id="10"))

    order: list[str] = []
    record, advance = db.record_message, db.advance_cursor

    async def watched_record(event, **kw):
        order.append("record")
        return await record(event, **kw)

    async def watched_advance(event):
        order.append("cursor")
        return await advance(event)

    db.record_message, db.advance_cursor = watched_record, watched_advance
    await captured(inbox)

    assert order.index("record") < order.index("cursor")


async def test_a_dropped_message_still_moves_the_cursor(inbox, provider, db):
    """Out of scope is a decision, not a failure. Leaving the cursor behind
    would make the sweep re-read it forever."""
    provider.emit(make_event(message_id="10", mention_type=None))

    await captured(inbox)

    assert await db.cursor_for("fake", "watched") == "10"


async def test_a_failure_leaves_the_cursor_where_it_was(inbox, provider, db):
    """The point of the ordering. If recording fails, the message has not been
    handled, and the sweep must still be able to find it."""

    async def refuses(event, **kw):
        raise RuntimeError("disk full")

    db.record_message = refuses
    provider.emit(make_event(message_id="10"))

    with pytest.raises(RuntimeError):
        await captured(inbox)

    assert await db.cursor_for("fake", "watched") is None


# --- the account's own messages: never work, always kept -------------------


async def test_the_accounts_own_message_never_becomes_work(inbox, db):
    """Whether the operator typed it or this process posted it. The agent once
    answered its own replies every minute in a real channel.

    "Own message" here means them talking to somebody — tagging nobody. An own
    message that *tags the account* is the deliberate exception, and has its
    own test below."""
    kept = await inbox._handle(
        make_event(message_id="mine", is_own=True, mention_type=None)
    )

    assert kept is None
    assert inbox.dropped == {"written by the watched account": 1}


async def test_the_accounts_own_message_is_stored_when_the_conversation_is_tracked(
    inbox, provider, db
):
    """It creates no task, but it is what *ends* one: the workflow reads the
    operator's own messages to know they have answered somebody. Dropped from
    scope and thrown away, that could never be known."""
    provider.emit(make_event(message_id="m1"))
    await captured(inbox)

    await inbox._handle(make_event(message_id="mine", text="để anh xem", is_own=True))

    stored = {m.provider_message_id for m in await db.messages()}
    assert "mine" in stored


async def test_a_colleague_repeating_our_sentence_is_still_a_mention(inbox, db):
    """Somebody else quoting the agent's question back is a person asking, and
    is not the account's own message."""
    kept = await inbox._handle(
        make_event(message_id="m9", text="cho anh xin cái correlationId nhé", author_name="dana")
    )

    assert kept is not None


# --- an answer to our own question ------------------------------------------


async def test_a_reply_to_something_we_posted_is_in_scope(provider, db, config):
    """The agent asks a question and has to be able to hear the answer.

    People reply in a thread; they do not tag you again to answer you. So the
    answer carries no mention, `mention_type` is None, and it was dropped as
    "does not address the account" — while being kept as context, which is why
    it left a trace and no work. The task stayed in `waiting_for_details` for
    ever and the cap on re-asking never fired, because no follow-up ever
    arrived. Found by the operator, in a real thread, on the third message.
    """
    from friday.outbox import Kind

    row = await db.queue_outbound(
        task_id=None,
        conversation=ConversationId("fake", "watched"),
        kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user",
        text="cho anh xin cái correlationId nhé",
    )
    await db.mark_outbound_sent(row.id, sent_message_id="ours-1")
    inbox = Inbox(provider=provider, db=db, config=config)

    kept = await inbox._handle(
        make_event(
            message_id="their-reply",
            text="correlationId là cái gì a nhỉ, e ko biết",
            mention_type=None,
            reply_to="ours-1",
        )
    )

    assert kept is not None
    assert inbox.dropped == {}


async def test_a_reply_to_someone_else_is_still_out_of_scope(provider, db, config):
    """Only a reply to *us* addresses us. Two colleagues talking in a watched
    channel are not asking the account anything."""
    inbox = Inbox(provider=provider, db=db, config=config)

    kept = await inbox._handle(
        make_event(
            message_id="m9",
            text="đúng rồi đó anh",
            mention_type=None,
            reply_to="someone-elses-message",
        )
    )

    assert kept is None
    assert inbox.dropped == {"does not address the account": 1}


async def test_a_reply_we_accepted_actually_reaches_the_queue(db, provider, config):
    """Two halves of one decision, and they disagreed.

    The inbox lets a reply-to-us through, and `untriaged_mentions` then asked
    the same question a second way — `mention_type IS NOT NULL` — and threw it
    away. The message was accepted, stored as work, and never queued: the
    agent asked, the reporter answered, and the answer sat in the table.
    """
    from friday.outbox import Kind

    row = await db.queue_outbound(
        task_id=None,
        conversation=ConversationId("fake", "watched"),
        kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user",
        text="cho anh xin cái correlationId nhé",
    )
    await db.mark_outbound_sent(row.id, sent_message_id="ours-1")
    inbox = Inbox(provider=provider, db=db, config=config)

    await inbox._handle(
        make_event(
            message_id="their-reply",
            text="đây a: abcdef01-2345-6789-abcd-ef0123456789",
            mention_type=None,
            reply_to="ours-1",
        )
    )

    queued = [e.provider_message_id for e in await db.untriaged_mentions()]
    assert "their-reply" in queued


async def test_context_is_still_never_queued(db, provider, config):
    """`context_only` stamps `triaged_at` on the way in, which is the whole of
    what keeps the seeded history out of the queue."""
    inbox = Inbox(provider=provider, db=db, config=config)

    await inbox._handle(make_event(message_id="chatter", mention_type=None))

    assert await db.untriaged_mentions() == []


async def test_a_message_this_process_posted_is_never_work_whichever_identity_sent_it(
    inbox, provider, db
):
    """Two Discord identities run in one process, and `is_own` only knows one.

    It is decided as `author.id == me.id` on the *user* gateway, so anything
    the **bot** posted reads as somebody else's message. The bot DMs the
    operator, that DM comes back through the user gateway, and a DM bypasses
    the channel whitelist — so the agent queued its own status report as work
    and produced a help-wanted saying "Nothing I can do with this" about its
    own liveness summary.

    `we_sent` is the guard for exactly this, matched on the id *or* the text
    so the echo cannot beat the outbox's write. Ticket 37 removed its only
    caller and left it dead.
    """
    from friday.outbox import Kind

    task = await db.create_task(
        conversation=ConversationId("fake", "watched"),
        type="devops.api_issue", state="pending", confidence=0.9, params={},
    )
    row = await db.queue_outbound(
        task_id=task.id, conversation=task.conversation, kind=Kind.HELP_WANTED,
        sender="discord_bot", text="Alive. 79 messages held, 3 tasks.",
    )
    await db.mark_outbound_sent(row.id, sent_message_id="bot-dm-1")

    # Comes back as an ordinary inbound mention: the bot is not the watched
    # account, so `is_own` is False.
    provider.emit(
        make_event(
            message_id="bot-dm-1",
            text="Alive. 79 messages held, 3 tasks.",
            author_id="the-bot",
            author_name="Friday",
            mention_type=MentionType.DM,
        )
    )

    events = await captured(inbox)

    assert events == [], "the agent queued its own message as work"
    assert await db.untriaged_mentions() == []


async def test_our_own_message_is_recognised_before_the_outbox_records_its_id(
    inbox, provider, db
):
    """The race `we_sent` was written to close: the outbox posts, the gateway
    delivers our own message back, and only then does the outbox record the id
    it got. In that window the id says nothing, so the text has to answer."""
    from friday.outbox import Kind

    task = await db.create_task(
        conversation=ConversationId("fake", "watched"),
        type="devops.api_issue", state="pending", confidence=0.9, params={},
    )
    await db.queue_outbound(
        task_id=task.id, conversation=task.conversation, kind=Kind.HELP_WANTED,
        sender="discord_bot", text="Alive. 79 messages held, 3 tasks.",
    )
    # No mark_outbound_sent: the id has not come back yet.

    provider.emit(
        make_event(
            message_id="not-recorded-yet",
            text="Alive. 79 messages held, 3 tasks.",
            author_id="the-bot",
            author_name="Friday",
            mention_type=MentionType.DM,
        )
    )

    assert await captured(inbox) == []


async def test_tagging_yourself_is_work_because_nobody_does_it_by_accident(
    inbox, provider, db
):
    """The only way to exercise the whole path — gateway, mention detection,
    whitelist, turn, reply threading — without a second Discord account.

    It worked until ticket 37 made the own-message rule unconditional, and
    what that ticket was closing is covered below rather than by this door
    staying shut.
    """
    provider.emit(
        make_event(
            message_id="self-tag",
            text="@Lee api checkout trả 500",
            is_own=True,
            mention_type=MentionType.DIRECT,
        )
    )

    events = await captured(inbox)

    assert [e.provider_message_id for e in events] == ["self-tag"]
    assert [m.provider_message_id for m in await db.untriaged_mentions()] == [
        "self-tag"
    ]


async def test_the_operator_talking_in_a_dm_is_still_not_work(inbox, provider, db):
    """The door the rule above must not open. Every message in a one-to-one DM
    carries `MentionType.DM` whether or not anyone was named, so counting a DM
    as a tag would make every "ok" the operator types open a task — the
    self-answering loop again, through a different door."""
    provider.emit(
        make_event(
            message_id="dm-ok",
            text="ok a e check rồi phản hồi a nhé",
            is_own=True,
            mention_type=MentionType.DM,
        )
    )

    assert await captured(inbox) == []
    assert await db.untriaged_mentions() == []
