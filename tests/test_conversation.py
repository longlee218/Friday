"""Ticket 15 — conversation identity.

A conversation id has to be unambiguous on its own. Everything downstream keys
off it, and a bare platform id says nothing about which platform it came from.
"""

from __future__ import annotations

import pytest

from conftest import make_event
from friday.kernel.domain.conversation import ConversationId, resolve


def test_a_thread_is_a_different_conversation_from_its_channel():
    """Seeding context from the parent would pull in messages nobody in the
    thread was reading."""
    channel = ConversationId("discord", "100")
    thread = ConversationId("discord", "100", "200")

    assert channel != thread
    assert str(channel) != str(thread)


def test_two_providers_sharing_a_number_are_different_conversations():
    assert ConversationId("discord", "100") != ConversationId("slack", "100")


@pytest.mark.parametrize(
    "conversation",
    [
        ConversationId("discord", "100"),
        ConversationId("discord", "100", "200"),
        ConversationId("slack", "C0FFEE", "1712.9981"),
    ],
)
def test_an_id_survives_being_stored_as_text(conversation):
    """It is one column in SQLite, so the text form is the identity."""
    assert ConversationId.parse(str(conversation)) == conversation


def test_the_send_target_is_the_thread_when_there_is_one():
    """A reply belongs in the thread it answers, not the parent channel."""
    assert ConversationId("discord", "100").target_id == "100"
    assert ConversationId("discord", "100", "200").target_id == "200"


def test_resolving_an_event_qualifies_it_by_provider():
    event = make_event(channel_id="100", thread_id="200")

    assert resolve(event) == ConversationId("fake", "100", "200")
