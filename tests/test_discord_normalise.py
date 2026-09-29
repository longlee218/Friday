"""Ticket 01 — turning a platform message into an InboundEvent.

Stubs stand in for platform objects: this tests the classification rules, not
the library.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from friday.kernel.domain.messages import MentionType
from friday.kernel.providers.discord.normalise import normalise

ME = 100
MY_ROLES = frozenset({200, 201})


def guild_channel(id=10, parent_id=None):
    return SimpleNamespace(id=id, parent_id=parent_id)


def dm_channel(id=20):
    """A one-to-one DM: exactly one recipient."""
    return SimpleNamespace(id=id, parent_id=None, recipient=SimpleNamespace(id=55))


def group_channel(id=30):
    """A group DM: several recipients, no single `recipient`."""
    return SimpleNamespace(id=id, parent_id=None, recipients=[SimpleNamespace(id=55)])


#: The default guild a message is in.
GUILD = SimpleNamespace(id=1)


def message(
    *,
    guild=GUILD,
    channel=None,
    mentions=(),
    role_mentions=(),
    content="hello",
    reference=None,
):
    return SimpleNamespace(
        id=999,
        guild=guild,
        channel=channel if channel is not None else guild_channel(),
        mentions=list(mentions),
        role_mentions=list(role_mentions),
        content=content,
        created_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
        author=SimpleNamespace(id=55, display_name="dana"),
        reference=reference,
    )


def normalised(msg):
    return normalise(msg, me_id=ME, my_role_ids=MY_ROLES)


def test_being_mentioned_directly_is_a_direct_mention():
    event = normalised(message(mentions=[SimpleNamespace(id=ME)]))

    assert event.mention_type is MentionType.DIRECT


def test_a_role_i_hold_being_mentioned_is_a_role_mention():
    event = normalised(message(role_mentions=[SimpleNamespace(id=200)]))

    assert event.mention_type is MentionType.ROLE


def test_a_role_i_do_not_hold_is_not_a_mention():
    event = normalised(message(role_mentions=[SimpleNamespace(id=999)]))

    assert event.mention_type is None


def test_any_direct_message_counts_even_without_an_explicit_mention():
    event = normalised(message(guild=None, channel=dm_channel()))

    assert event.mention_type is MentionType.DM


def test_a_message_addressed_to_nobody_relevant_is_not_a_mention():
    event = normalised(message())

    assert event.mention_type is None


def test_a_direct_mention_outranks_a_role_mention():
    event = normalised(
        message(
            mentions=[SimpleNamespace(id=ME)], role_mentions=[SimpleNamespace(id=200)]
        )
    )

    assert event.mention_type is MentionType.DIRECT


def test_a_thread_message_records_the_parent_channel_and_the_thread():
    event = normalised(
        message(
            channel=guild_channel(id=77, parent_id=10),
            mentions=[SimpleNamespace(id=ME)],
        )
    )

    assert (event.channel_id, event.thread_id) == ("10", "77")


def test_a_channel_message_has_no_thread():
    event = normalised(message(mentions=[SimpleNamespace(id=ME)]))

    assert (event.channel_id, event.thread_id) == ("10", None)


def test_the_message_details_are_carried_across():
    event = normalised(message(mentions=[SimpleNamespace(id=ME)], content="it is down"))

    assert event.provider == "discord"
    assert event.provider_message_id == "999"
    assert event.text == "it is down"
    assert event.author_id == "55"
    assert event.author_name == "dana"


def test_a_group_chat_message_without_a_mention_is_not_a_mention():
    """A group chat is not a DM: most of its traffic is not addressed to us."""
    event = normalised(message(guild=None, channel=group_channel()))

    assert event.mention_type is None


def test_being_mentioned_in_a_group_chat_is_a_direct_mention():
    event = normalised(
        message(
            guild=None,
            channel=group_channel(),
            mentions=[SimpleNamespace(id=ME)],
        )
    )

    assert event.mention_type is MentionType.DIRECT


def test_a_group_chat_reports_its_own_channel_id_so_it_can_be_whitelisted():
    event = normalised(
        message(
            guild=None, channel=group_channel(id=30), mentions=[SimpleNamespace(id=ME)]
        )
    )

    assert (event.channel_id, event.thread_id) == ("30", None)


def test_a_reply_records_what_it_replies_to():
    event = normalised(message(reference=SimpleNamespace(message_id=777)))

    assert event.reply_to == "777"


def test_a_message_that_is_not_a_reply_records_nothing():
    event = normalised(message())

    assert event.reply_to is None
