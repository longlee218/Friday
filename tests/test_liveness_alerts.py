"""Ticket 09 — the human finds out.

A connection that died and a quiet afternoon look identical from outside. This
is the difference, and it has to arrive somewhere the operator will see it
rather than in a log they are not reading.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from friday.kernel.ops.liveness import Liveness
from friday.kernel.outbox import Kind

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


class Gateway:
    """Stands in for the provider's connection."""

    def __init__(self, down_since=None):
        self.down_since = down_since


def watching(db, gateway, **kw):
    kw.setdefault("down_after_seconds", 300)
    # Off unless a test is about it, or every check at noon also summarises.
    kw.setdefault("summary_at_hour", None)
    return Liveness(db=db, gateway=gateway, operator="discord_bot", **kw)


async def test_a_brief_blip_is_not_worth_telling_anyone(db):
    """Discord drops and resumes constantly. Alerting on that trains the
    operator to ignore the alert that matters."""
    watch = watching(db, Gateway(down_since=NOW - timedelta(seconds=30)))

    await watch.check(now=NOW)

    assert await db.outbound() == []


async def test_a_connection_down_too_long_reaches_the_operator(db):
    watch = watching(db, Gateway(down_since=NOW - timedelta(minutes=20)))

    await watch.check(now=NOW)

    (alert,) = await db.outbound()
    assert alert.kind == Kind.ALERT
    assert alert.sender == "discord_bot"
    assert "20m" in alert.text


async def test_it_is_said_once_and_not_every_minute(db):
    watch = watching(db, Gateway(down_since=NOW - timedelta(minutes=20)))

    await watch.check(now=NOW)
    await watch.check(now=NOW + timedelta(minutes=1))
    await watch.check(now=NOW + timedelta(minutes=2))

    assert len(await db.outbound()) == 1


async def test_coming_back_is_worth_saying_too(db):
    """Otherwise the operator is left believing it is still down."""
    gateway = Gateway(down_since=NOW - timedelta(minutes=20))
    watch = watching(db, gateway)
    await watch.check(now=NOW)

    gateway.down_since = None
    await watch.check(now=NOW + timedelta(minutes=1))

    assert [r.kind for r in await db.outbound()] == [Kind.ALERT, Kind.ALERT]
    assert "back" in (await db.outbound())[1].text.lower()


async def test_recovery_is_not_announced_when_nothing_was_wrong(db):
    watch = watching(db, Gateway(down_since=None))

    await watch.check(now=NOW)

    assert await db.outbound() == []


async def test_a_daily_summary_says_what_happened(db, inbox, provider):
    from conftest import captured, make_event

    provider.emit(make_event(message_id="10"))
    await captured(inbox)

    watch = watching(db, Gateway(), summary_at_hour=9)
    await watch.check(now=NOW.replace(hour=9))

    (summary,) = await db.outbound()
    assert "1" in summary.text


async def test_the_summary_comes_once_a_day(db):
    watch = watching(db, Gateway(), summary_at_hour=9)

    await watch.check(now=NOW.replace(hour=9))
    await watch.check(now=NOW.replace(hour=9, minute=30))
    await watch.check(now=NOW.replace(hour=10))

    assert len(await db.outbound()) == 1
