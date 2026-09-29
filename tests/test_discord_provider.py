"""Ticket 01 — the adapter's contract with the platform library.

These pin integration facts that stubs elsewhere cannot: the library dispatches
to handlers by attribute name, so a handler bound under any other name is never
called and the process simply goes quiet.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from friday.kernel.domain.conversation import ConversationId
from friday.kernel.providers import Provider
from friday.kernel.providers.discord.user import DiscordUserProvider


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


def test_the_adapter_implements_everything_the_protocol_declares():
    """`Provider` is a Protocol, so nothing checks this at import or at
    construction — a missing method surfaces only when something reaches for
    it, at run time, in production. `history` and `recent` were both absent for
    three tickets and the recovery sweep never ran once.

    Read from the protocol, not from a list written beside it. The list used to
    be written out here, and the two drifted: `send` was in this test and not
    in `Provider`, while the outbox called it on every delivery.
    """
    import typing

    provider = DiscordUserProvider("token", client=stub_client())
    declared = typing.get_protocol_members(Provider)

    assert "send" in declared, "the outbox calls it; the protocol must declare it"
    missing = sorted(name for name in declared if not hasattr(provider, name))
    assert missing == []


class StubHistory:
    """A channel whose history() replays a canned list, recording its filters."""

    def __init__(self, *messages):
        self.id = 55
        self._messages = list(messages)
        self.calls: list[dict] = []

    #: What the real library does when nobody says (ticket 02, D9). The fake
    #: used to read a missing `limit` as "all of them", which is the one
    #: reading that makes the bug invisible: in production it means a hundred,
    #: counted from whichever end `oldest_first` picked.
    LIBRARY_DEFAULT_LIMIT = 100

    def history(self, **kwargs):
        self.calls.append(kwargs)
        assert "limit" in kwargs, (
            "every call must say its page size — see `_replay`'s D9. Without "
            "this the library's default of 100 applies and no test can see it."
        )
        messages = self._messages
        if kwargs.get("oldest_first") is False:
            messages = list(reversed(messages))
        limit = kwargs["limit"]
        if limit is None:
            limit = len(messages)

        async def stream():
            for message in messages[:limit]:
                yield message

        return stream()


def stub_message(message_id, text="hello", author_id=2, channel=None, created_at=None):
    return SimpleNamespace(
        id=message_id,
        content=text,
        clean_content=text,
        author=SimpleNamespace(id=author_id, display_name="reporter", bot=False),
        channel=channel or SimpleNamespace(id=55, type=None),
        guild=None,
        created_at=created_at or datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
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

    seen = [event async for event in provider.history("55", after="9", since=None)]

    assert [e.provider_message_id for e in seen] == ["10", "11"]
    assert channel.calls[0]["oldest_first"] is True
    assert channel.calls[0]["after"].id == 9


async def test_history_from_the_beginning_passes_no_cursor():
    channel = StubHistory(stub_message(10))
    client = stub_client()
    client.get_channel = lambda _id: channel
    provider = DiscordUserProvider("token", client=client)

    [event async for event in provider.history("55", after=None, since=None)]

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


def test_the_unofficial_library_stays_in_one_module():
    """`docs/DESIGN.md` records `discord-self` as an accepted risk and names the
    mitigation: "isolating the user-side in one module". A mitigation nothing
    checks is a mitigation on paper — this is the same enforcement ticket 23
    used for the agent SDK, for a dependency with a worse prognosis.

    `__init__.py` must stay empty for it to hold. Importing any submodule runs
    the parent's `__init__.py` first, and a re-export there is eager, so a
    convenience import would pull the library back into `bot.py` — the
    *official* identity — and into normalisation, which touches no Discord type
    at all.
    """
    import subprocess

    allowed = {"friday/kernel/providers/discord/user.py"}
    hits = subprocess.run(
        ["grep", "-rlE", r"^\s*(from discord_self|import discord_self)\b", "friday/"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()

    assert set(hits) <= allowed, f"unexpected importer: {set(hits) - allowed}"


# --- ticket 02 (board `work-that-has-gone-cold`): the cold cursor ------------


def _at(hours_ago: float):
    from datetime import timedelta

    return datetime(2026, 9, 15, 12, 0, tzinfo=UTC) - timedelta(hours=hours_ago)


async def test_a_cold_cursor_reads_the_newest_end_and_stops_at_the_lookback():
    """D8. The bug this ticket names is that `after=None` read the *oldest*
    hundred messages in the channel — the day it was created, not the day
    before. Given a lookback it reads the other end, and stops."""
    channel = StubHistory(
        stub_message(10, created_at=_at(40)),
        stub_message(11, created_at=_at(30)),
        stub_message(12, created_at=_at(10)),
        stub_message(13, created_at=_at(1)),
    )
    client = stub_client()
    client.get_channel = lambda _id: channel
    provider = DiscordUserProvider("token", client=client)

    seen = [event async for event in provider.history("55", after=None, since=_at(24))]

    assert [e.provider_message_id for e in seen] == ["12", "13"]
    assert channel.calls[0]["oldest_first"] is False


async def test_a_cold_cursor_hands_them_over_oldest_first():
    """Read backwards, delivered forwards. Discord returns newest first when
    reading this way and a transcript out of order reads to a model as a
    different conversation — the same reversal `recent` makes."""
    channel = StubHistory(
        stub_message(10, created_at=_at(3)),
        stub_message(11, created_at=_at(2)),
        stub_message(12, created_at=_at(1)),
    )
    client = stub_client()
    client.get_channel = lambda _id: channel
    provider = DiscordUserProvider("token", client=client)

    seen = [event async for event in provider.history("55", after=None, since=_at(24))]

    assert [e.provider_message_id for e in seen] == ["10", "11", "12"]


async def test_a_cold_cursor_with_no_lookback_reads_as_it_always_did():
    """`max_message_age` unset leaves this ticket with nothing to bound the
    read by, and both ways of bounding it — read everything, read nothing —
    are options the operator rejected. So the behaviour is unchanged and the
    page size is merely *said out loud*, which is all D9 asked for. Ticket
    02 carries the leftover question."""
    channel = StubHistory(stub_message(10), stub_message(11))
    client = stub_client()
    client.get_channel = lambda _id: channel
    provider = DiscordUserProvider("token", client=client)

    seen = [event async for event in provider.history("55", after=None, since=None)]

    assert [e.provider_message_id for e in seen] == ["10", "11"]
    assert channel.calls[0]["oldest_first"] is True
    assert channel.calls[0]["limit"] == 100


async def test_every_read_of_a_channel_says_its_page_size():
    """D9. The hundred-message default was never chosen by any line of code
    here, and it was invisible because nothing asserted the call. Delete the
    `limit` from `_replay` and `StubHistory.history` fails outright."""
    channel = StubHistory(stub_message(10, created_at=_at(1)))
    client = stub_client()
    client.get_channel = lambda _id: channel
    provider = DiscordUserProvider("token", client=client)

    [e async for e in provider.history("55", after="9", since=None)]
    [e async for e in provider.history("55", after=None, since=None)]
    [e async for e in provider.history("55", after=None, since=_at(24))]
    [
        e
        async for e in provider.recent(
            ConversationId("discord", "55"), before="13", limit=2
        )
    ]

    assert len(channel.calls) == 4
    assert all("limit" in call for call in channel.calls)
    assert [call["limit"] for call in channel.calls] == [100, 100, None, 2]


async def test_a_cursor_beats_a_lookback():
    """Precedence, written down in the protocol and pinned here. The inbox
    never sends both today — it computes `since` only when `after` is None —
    but "what happens if both arrive" is a question a second provider will
    ask, and an undefined answer is how two implementations disagree."""
    channel = StubHistory(
        stub_message(10, created_at=_at(40)),
        stub_message(11, created_at=_at(1)),
    )
    client = stub_client()
    client.get_channel = lambda _id: channel
    provider = DiscordUserProvider("token", client=client)

    [e async for e in provider.history("55", after="9", since=_at(24))]

    assert channel.calls[0]["oldest_first"] is True, "read forwards, not back"
    assert channel.calls[0]["after"].id == 9
