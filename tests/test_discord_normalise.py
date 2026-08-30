"""Ticket 01 — turning a platform message into an InboundEvent.

Stubs stand in for platform objects: this tests the classification rules, not
the library.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from friday.models import MentionType
from friday.providers.discord.normalise import normalise

ME = 100
MY_ROLES = frozenset({200, 201})


def message(
    *,
    guild=SimpleNamespace(id=1),
    channel=SimpleNamespace(id=10, parent_id=None),
    mentions=(),
    role_mentions=(),
    content="hello",
):
    return SimpleNamespace(
        id=999,
        guild=guild,
        channel=channel,
        mentions=list(mentions),
        role_mentions=list(role_mentions),
        content=content,
        created_at=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
        author=SimpleNamespace(id=55, display_name="dana"),
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
    event = normalised(message(guild=None))

    assert event.mention_type is MentionType.DM


def test_a_message_addressed_to_nobody_relevant_is_not_a_mention():
    event = normalised(message())

    assert event.mention_type is None


def test_a_direct_mention_outranks_a_role_mention():
    event = normalised(
        message(mentions=[SimpleNamespace(id=ME)], role_mentions=[SimpleNamespace(id=200)])
    )

    assert event.mention_type is MentionType.DIRECT


def test_a_thread_message_records_the_parent_channel_and_the_thread():
    event = normalised(
        message(
            channel=SimpleNamespace(id=77, parent_id=10),
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
