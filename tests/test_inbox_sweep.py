"""Ticket 03 — the recovery path, driven through the Provider seam."""

from __future__ import annotations

from conftest import captured, make_event
from friday.kernel.domain.messages import MentionType


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

    assert provider.history_calls == [("watched", "100", None)]


async def test_a_sweep_asks_from_the_beginning_when_there_is_no_cursor(
    inbox, provider
):
    await inbox.sweep_once()

    assert provider.history_calls == [("watched", None, None)]


async def test_a_sweep_covers_only_watched_channels(inbox, provider):
    provider.emit_history("elsewhere", make_event(message_id="100"))

    swept = await inbox.sweep_once()

    assert swept == []
    assert [c for c, _, _ in provider.history_calls] == ["watched"]


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

    from friday.kernel.inbox import Inbox

    provider.keep_open = True
    provider.emit_history("watched", make_event(message_id="100"))
    inbox = Inbox(provider=provider, db=db, config=config, sweep_interval_seconds=600)

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

    from friday.kernel.inbox import Inbox

    provider.keep_open = True
    provider.emit_history("watched", make_event(message_id="100"))
    inbox = Inbox(provider=provider, db=db, config=config, sweep_interval_seconds=0.01)

    seen = []

    async def consume():
        async for event in inbox.stream():
            seen.append(event)
            provider.close()

    await asyncio.wait_for(asyncio.create_task(consume()), timeout=2)

    assert [e.provider_message_id for e in seen] == ["100"]


# --- ticket 02 (board `work-that-has-gone-cold`): the cold cursor ------------


def _cold(provider, db, config, *, lookback):
    from friday.kernel.inbox import Inbox

    return Inbox(
        provider=provider, db=db, config=config, cold_start_lookback=lookback
    )


async def test_a_cold_cursor_looks_back_as_far_as_the_lookback(provider, db, config):
    """D8. With no cursor there is no message to start after, so the sweep
    says how far back it is willing to read instead of reading from the day
    the channel was created."""
    from datetime import datetime, timedelta, timezone

    before = datetime.now(timezone.utc)
    await _cold(provider, db, config, lookback=24 * 3600).sweep_once()

    (channel, after, since) = provider.history_calls[0]
    assert (channel, after) == ("watched", None)
    assert since is not None
    assert before - timedelta(hours=24, seconds=5) <= since <= before - timedelta(
        hours=23, minutes=59
    )


async def test_a_warm_cursor_asks_after_it_and_names_no_lookback(provider, db, config):
    """The lookback answers "where do I start when there is nothing to start
    after". A cursor is that something, so it wins and the lookback is not
    consulted — otherwise a channel quiet for longer than the lookback would
    have its own cursor overruled."""
    await db.advance_cursor(make_event(message_id="100"))

    await _cold(provider, db, config, lookback=24 * 3600).sweep_once()

    assert provider.history_calls == [("watched", "100", None)]


async def test_with_no_lookback_a_cold_cursor_still_reads_from_the_beginning(
    provider, db, config
):
    """`max_message_age` unset means no cutoff, and the lookback is that same
    number — so unset here means the same thing it means there, rather than a
    second default nobody chose."""
    await _cold(provider, db, config, lookback=None).sweep_once()

    assert provider.history_calls == [("watched", None, None)]


async def test_a_cold_cursor_says_so_in_the_log(provider, db, config, caplog):
    """D11. The residual failure of this design is downtime longer than the
    lookback: the gap beyond it is dropped. That is acceptable only because
    it is visible, so a boot that reads from a lookback rather than a cursor
    has to say which channel and how far back."""
    import logging

    with caplog.at_level(logging.INFO, logger="friday.kernel.inbox"):
        await _cold(provider, db, config, lookback=24 * 3600).sweep_once()

    said = "\n".join(r.getMessage() for r in caplog.records)
    assert "watched" in said
    assert "24" in said or "86400" in said


async def test_a_cold_cursor_is_reported_once_not_every_sweep(provider, db, config):
    """D11 asks for one line at boot. A cold cursor is not a one-shot state:
    a watched channel whose lookback holds no messages records no cursor, so
    it is cold again on the next sweep and on every sweep after it. Told every
    five minutes, it becomes the line a person scrolls past."""
    import logging

    inbox = _cold(provider, db, config, lookback=24 * 3600)
    with caplog_at(logging, "friday.kernel.inbox") as records:
        await inbox.sweep_once()
        await inbox.sweep_once()
        await inbox.sweep_once()

    cold = [r for r in records if "has no cursor" in r.getMessage()]
    assert len(cold) == 1


class caplog_at:
    """A handler on one logger, because `caplog` is per-test and this needs
    the count across three calls in one."""

    def __init__(self, logging_module, name):
        self._logging = logging_module
        self._logger = logging_module.getLogger(name)
        self.records = []

    def __enter__(self):
        outer = self

        class Collect(self._logging.Handler):
            def emit(self, record):
                outer.records.append(record)

        self._handler = Collect()
        self._previous = self._logger.level
        self._logger.setLevel(self._logging.INFO)
        self._logger.addHandler(self._handler)
        return self.records

    def __exit__(self, *exc):
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._previous)
        return False
