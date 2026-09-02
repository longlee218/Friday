"""A working agent on a quiet day and a dead one look identical from outside."""

from __future__ import annotations

from types import SimpleNamespace

from conftest import captured, make_event
from friday.domain.conversation import ConversationId
from friday.ops.liveness import Heartbeat
from friday.outbox import Kind


async def test_a_quiet_agent_still_says_it_is_alive(db):
    line = await Heartbeat(db=db).summary()

    assert "alive" in line
    assert "messages 0" in line
    assert "last never" in line


async def test_the_beat_reports_what_is_actually_stored(inbox, provider, db):
    provider.emit(make_event(message_id="10"))
    await captured(inbox)

    line = await Heartbeat(db=db).summary()

    assert "messages 1" in line
    assert "untriaged 1" in line


async def test_a_message_nobody_could_deliver_is_shouted_about(db):
    """The only way anyone learns about it is by being told."""
    task = await db.create_task(
        conversation=ConversationId("fake", "watched"), type="api_issue",
        state="needs_human", confidence=0.9, params={},
    )
    row = await db.queue_outbound(
        task_id=task.id, conversation=ConversationId("fake", "watched"),
        kind=Kind.ASK_FOR_DETAILS, sender="discord_user", text="which environment?",
    )
    await db.fail_outbound(row.id, "discord said no")

    assert "1 FAILED TO SEND" in await Heartbeat(db=db).summary()


async def test_it_reports_how_many_arrived_since_the_last_beat(inbox, provider, db):
    beat = Heartbeat(db=db)
    await beat.summary()

    provider.emit(make_event(message_id="10"))
    await captured(inbox)

    assert "(+1)" in await beat.summary()


async def test_the_daily_summary_survives_a_restart(db):
    """The guard was an attribute, so every start of the process was a fresh
    day: restart four times and the operator gets four "Alive." messages —
    and with `capture_own_messages` on, four model calls classifying them. A
    crash loop would have sent one per attempt.

    SQLite is the only state store. The outbox row is already the record of
    having said it, so the question is asked of the row.
    """
    from datetime import datetime, timezone

    from friday.ops.liveness import Liveness
    from friday.outbox import Kind

    noon = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

    def fresh_process():
        return Liveness(db=db, gateway=SimpleNamespace(down_since=None))

    await fresh_process()._summary(noon)
    await fresh_process()._summary(noon)
    await fresh_process()._summary(noon)

    summaries = [r for r in await db.outbound() if r.kind == Kind.SUMMARY]
    assert len(summaries) == 1, "a restart sent the day's summary again"


async def test_a_new_day_is_summarised_again(db):
    """Relative to the real clock, not fixed dates: the outbox stamps rows
    with real time, so a hard-coded "yesterday" becomes "today" the moment the
    calendar catches up — this test failed for exactly that reason the day
    after it was written."""
    from datetime import datetime, timedelta, timezone

    from friday.ops.liveness import Liveness
    from friday.outbox import Kind

    today_noon = datetime.now(timezone.utc).replace(hour=12)
    liveness = Liveness(db=db, gateway=SimpleNamespace(down_since=None))
    await liveness._summary(today_noon)
    await liveness._summary(today_noon + timedelta(days=1))

    assert len([r for r in await db.outbound() if r.kind == Kind.SUMMARY]) == 2
