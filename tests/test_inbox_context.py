"""Ticket 03 — conversation context, gathered lazily per session.

Nothing is stored until a conversation produces its first mention. Most traffic
in a watched channel never involves us, and retaining all of it to answer
questions about a fraction of it is not a trade worth making.
"""

from __future__ import annotations

from conftest import captured, make_event


async def test_a_first_mention_seeds_the_conversations_recent_history(
    inbox, provider, db
):
    provider.emit_recent(
        "watched",
        make_event(message_id="10", text="deploy went out", mention_type=None),
        make_event(message_id="20", text="anyone seeing errors?", mention_type=None),
    )
    provider.emit(make_event(message_id="30"))

    await captured(inbox)

    stored = [m.text for m in await db.messages()]
    assert "deploy went out" in stored
    assert "anyone seeing errors?" in stored


async def test_the_mention_itself_is_kept_as_context(inbox, provider, db):
    provider.emit(make_event(message_id="30", text="is checkout broken?"))

    await captured(inbox)

    assert "is checkout broken?" in [m.text for m in await db.messages()]


async def test_a_conversation_is_seeded_only_once(inbox, provider, db):
    provider.emit(make_event(message_id="30"))
    provider.emit(make_event(message_id="40"))

    await captured(inbox)

    assert len(provider.recent_calls) == 1


async def test_nothing_is_stored_for_a_conversation_that_never_mentions_us(
    inbox, provider, db
):
    """The reason we do not simply store every message in every channel."""
    provider.emit(make_event(message_id="10", mention_type=None))
    provider.emit(make_event(message_id="20", mention_type=None))

    await captured(inbox)

    assert await db.messages() == []
    assert provider.recent_calls == []


async def test_later_messages_in_a_seeded_conversation_are_kept_even_without_a_mention(
    inbox, provider, db
):
    """'still broken btw' is the message that makes the next one classifiable."""
    provider.emit(make_event(message_id="30"))
    provider.emit(make_event(message_id="40", text="still broken btw", mention_type=None))

    await captured(inbox)

    assert "still broken btw" in [m.text for m in await db.messages()]


async def test_a_thread_is_seeded_from_the_thread_not_its_parent_channel(
    inbox, provider
):
    provider.emit(make_event(message_id="30", channel_id="watched", thread_id="t1"))

    await captured(inbox)

    assert [c for c, _, _ in provider.recent_calls] == ["t1"]


async def test_seeding_asks_for_messages_before_the_mention(inbox, provider):
    provider.emit(make_event(message_id="30"))

    await captured(inbox)

    conversation, before, limit = provider.recent_calls[0]
    assert (conversation, before) == ("watched", "30")
    assert limit > 0


async def test_our_own_messages_never_trigger_work(inbox, provider, db):
    """Otherwise the agent answers its own replies, forever."""
    provider.emit(make_event(message_id="30", is_own=True, mention_type=None))

    assert await captured(inbox) == []
    assert await db.mentions() == []


async def test_our_own_messages_are_kept_as_context_once_a_conversation_matters(
    inbox, provider, db
):
    """The responder learns tone from real replies, and a stored conversation
    missing one side of itself reads strangely to a model."""
    provider.emit(make_event(message_id="30"))
    provider.emit(
        make_event(message_id="40", text="checking now", is_own=True)
    )

    await captured(inbox)

    assert "checking now" in [m.text for m in await db.messages()]


async def test_our_own_messages_are_not_kept_in_conversations_that_never_asked_us(
    inbox, provider, db
):
    provider.emit(
        make_event(
            message_id="30", text="just chatting", is_own=True, mention_type=None
        )
    )

    await captured(inbox)

    assert await db.messages() == []
