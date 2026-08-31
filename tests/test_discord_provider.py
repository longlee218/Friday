"""Ticket 01 — the adapter's contract with the platform library.

These pin integration facts that stubs elsewhere cannot: the library dispatches
to handlers by attribute name, so a handler bound under any other name is never
called and the process simply goes quiet.
"""

from __future__ import annotations

from types import SimpleNamespace

from friday.providers.discord import DiscordUserProvider


def test_the_message_handler_is_bound_where_dispatch_will_find_it():
    client = SimpleNamespace()

    DiscordUserProvider("token", client=client)

    # discord dispatch resolves "on_" + event as an attribute of the client
    assert callable(getattr(client, "on_message", None))


def test_the_ready_handler_is_bound_where_dispatch_will_find_it():
    client = SimpleNamespace()

    DiscordUserProvider("token", client=client)

    assert callable(getattr(client, "on_ready", None))


class StubUser:
    def __init__(self, id):
        self.id = id


def stub_client(me_id=1):
    client = SimpleNamespace()
    client.user = StubUser(me_id)
    return client


def own_message(me_id=1):
    return SimpleNamespace(
        id=1,
        guild=None,
        channel=SimpleNamespace(id=9, parent_id=None, recipient=StubUser(2)),
        mentions=[],
        role_mentions=[],
        content="testing",
        created_at=None,
        author=SimpleNamespace(id=me_id, display_name="me"),
    )


async def test_our_own_messages_are_delivered_and_marked_as_ours():
    """The provider reports who wrote it. Whether that means anything is the
    inbox's decision — and the responder needs these for tone examples."""
    client = stub_client()
    provider = DiscordUserProvider("token", client=client)

    await client.on_message(own_message())

    assert provider._incoming.get_nowait().is_own is True


async def test_someone_elses_message_is_not_marked_as_ours():
    client = stub_client()
    provider = DiscordUserProvider("token", client=client)
    message = own_message()
    message.author.id = 999

    await client.on_message(message)

    assert provider._incoming.get_nowait().is_own is False


def test_a_reply_goes_to_the_thread_when_the_message_was_in_one():
    from friday.providers.discord import reply_target_id

    from conftest import make_event

    event = make_event(channel_id="chan", thread_id="thread")

    assert reply_target_id(event) == "thread"


def test_a_reply_goes_to_the_channel_when_there_was_no_thread():
    from friday.providers.discord import reply_target_id

    from conftest import make_event

    event = make_event(channel_id="chan", thread_id=None)

    assert reply_target_id(event) == "chan"
