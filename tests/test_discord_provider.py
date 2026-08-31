"""Ticket 01 — the adapter's contract with the platform library.

These pin integration facts that stubs elsewhere cannot: the library dispatches
to handlers by attribute name, so a handler bound under any other name is never
called and the process simply goes quiet.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from friday.conversation import ConversationId
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


def test_the_adapter_implements_everything_the_inbox_will_call():
    """`Provider` is a Protocol, so nothing checks this at import or at
    construction — a missing method surfaces only when the inbox reaches for it,
    at runtime, in production. `history` and `recent` were both absent for three
    tickets and the recovery sweep never ran once."""
    provider = DiscordUserProvider("token", client=stub_client())

    missing = [
        name
        for name in ("name", "reconnected", "stream", "history", "recent", "send")
        if not hasattr(provider, name)
    ]
    assert missing == []


class StubHistory:
    """A channel whose history() replays a canned list, recording its filters."""

    def __init__(self, *messages):
        self.id = 55
        self._messages = list(messages)
        self.calls: list[dict] = []

    def history(self, **kwargs):
        self.calls.append(kwargs)
        messages = self._messages
        if kwargs.get("oldest_first") is False:
            messages = list(reversed(messages))

        async def stream():
            for message in messages[: kwargs.get("limit") or len(messages)]:
                yield message

        return stream()


def stub_message(message_id, text="hello", author_id=2, channel=None):
    return SimpleNamespace(
        id=message_id,
        content=text,
        clean_content=text,
        author=SimpleNamespace(id=author_id, display_name="reporter", bot=False),
        channel=channel or SimpleNamespace(id=55, type=None),
        guild=None,
        created_at=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
        mentions=[],
        role_mentions=[],
    )


async def test_history_replays_a_channel_after_a_cursor():
    """This is the recovery sweep. Without it a disconnection loses messages
    permanently, which is the whole of ticket 03."""
    channel = StubHistory(stub_message(10), stub_message(11))
    client = stub_client()
    client.get_channel = lambda _id: channel
    provider = DiscordUserProvider("token", client=client)

    seen = [event async for event in provider.history("55", after="9")]

    assert [e.provider_message_id for e in seen] == ["10", "11"]
    assert channel.calls[0]["oldest_first"] is True
    assert channel.calls[0]["after"].id == 9


async def test_history_from_the_beginning_passes_no_cursor():
    channel = StubHistory(stub_message(10))
    client = stub_client()
    client.get_channel = lambda _id: channel
    provider = DiscordUserProvider("token", client=client)

    [event async for event in provider.history("55", after=None)]

    assert channel.calls[0]["after"] is None


async def test_recent_reads_backwards_from_a_message():
    """Seeding context: what was said before a conversation first involved us."""
    channel = StubHistory(stub_message(10), stub_message(11), stub_message(12))
    client = stub_client()
    client.get_channel = lambda _id: channel
    provider = DiscordUserProvider("token", client=client)

    seen = [
        event
        async for event in provider.recent(
            ConversationId("discord", "55"), before="13", limit=2
        )
    ]

    assert [e.provider_message_id for e in seen] == ["11", "12"]
    assert channel.calls[0]["before"].id == 13
    assert channel.calls[0]["limit"] == 2


async def test_the_provider_knows_when_it_went_down():
    """Nothing tracked this. A dead gateway and a quiet channel produced the
    same observable: no messages."""
    client = stub_client()
    provider = DiscordUserProvider("token", client=client)

    assert provider.down_since is None

    await client.on_disconnect()
    assert provider.down_since is not None

    await client.on_ready()
    assert provider.down_since is None


async def test_a_second_drop_does_not_restart_the_clock():
    """discord.py fires on_disconnect on every reconnection attempt. Taking the
    latest would reset the timer forever and the alert would never fire."""
    client = SimpleNamespace()
    client.user = StubUser(1)
    provider = DiscordUserProvider("token", client=client)

    await client.on_disconnect()
    first = provider.down_since
    await client.on_disconnect()

    assert provider.down_since == first
