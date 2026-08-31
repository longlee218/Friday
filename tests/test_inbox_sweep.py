"""Ticket 03 — the recovery path, driven through the Provider seam."""

from __future__ import annotations

from conftest import captured, make_event
from friday.models import MentionType


async def test_a_sweep_captures_messages_the_live_path_never_delivered(
    inbox, provider, db
):
    provider.emit_history("watched", make_event(message_id="100"))

    swept = await inbox.sweep_once()

    assert [e.provider_message_id for e in swept] == ["100"]
    assert len(await db.mentions()) == 1


async def test_a_message_already_seen_live_is_not_captured_again_by_a_sweep(
    inbox, provider
):
    """The whole point of running two delivery paths safely."""
    event = make_event(message_id="100")
    provider.emit(event)
    provider.emit_history("watched", event)

    live = await captured(inbox)
    swept = await inbox.sweep_once()

    assert [e.provider_message_id for e in live] == ["100"]
    assert swept == []


async def test_a_sweep_asks_only_for_messages_after_the_cursor(inbox, provider):
    provider.emit(make_event(message_id="100"))
    await captured(inbox)

    await inbox.sweep_once()

    assert provider.history_calls == [("watched", "100")]


async def test_a_sweep_asks_from_the_beginning_when_there_is_no_cursor(
    inbox, provider
):
    await inbox.sweep_once()

    assert provider.history_calls == [("watched", None)]


async def test_a_sweep_covers_only_watched_channels(inbox, provider):
    provider.emit_history("elsewhere", make_event(message_id="100"))

    swept = await inbox.sweep_once()

    assert swept == []
    assert [c for c, _ in provider.history_calls] == ["watched"]


async def test_a_swept_message_that_does_not_address_the_account_is_dropped(
    inbox, provider, db
):
    provider.emit_history(
        "watched", make_event(message_id="100", mention_type=None)
    )

    assert await inbox.sweep_once() == []
    assert await db.mentions() == []


async def test_a_sweep_advances_the_cursor(inbox, provider, db):
    provider.emit_history("watched", make_event(message_id="100"))

    await inbox.sweep_once()

    assert await db.cursor_for("fake", "watched") == "100"


async def test_a_sweep_keeps_a_role_mention(inbox, provider):
    provider.emit_history(
        "watched", make_event(message_id="100", mention_type=MentionType.ROLE)
    )

    swept = await inbox.sweep_once()

    assert [e.mention_type for e in swept] == [MentionType.ROLE]


async def test_a_reconnect_triggers_a_sweep_without_waiting_for_the_timer(
    provider, db, config
):
    """After an outage the gap should close immediately, not in five minutes."""
    import asyncio
    import dataclasses

    from friday.inbox import Inbox

    provider.keep_open = True
    provider.emit_history("watched", make_event(message_id="100"))
    slow = dataclasses.replace(config, sweep_interval_seconds=600)
    inbox = Inbox(provider=provider, db=db, config=slow)

    seen = []

    async def consume():
        async for event in inbox.stream():
            seen.append(event)
            provider.close()

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    provider.reconnected.set()
    await asyncio.wait_for(task, timeout=2)

    assert [e.provider_message_id for e in seen] == ["100"]


async def test_the_sweep_also_runs_on_a_timer(provider, db, config):
    import asyncio
    import dataclasses

    from friday.inbox import Inbox

    provider.keep_open = True
    provider.emit_history("watched", make_event(message_id="100"))
    quick = dataclasses.replace(config, sweep_interval_seconds=0.01)
    inbox = Inbox(provider=provider, db=db, config=quick)

    seen = []

    async def consume():
        async for event in inbox.stream():
            seen.append(event)
            provider.close()

    await asyncio.wait_for(asyncio.create_task(consume()), timeout=2)

    assert [e.provider_message_id for e in seen] == ["100"]
