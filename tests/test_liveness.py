"""A working agent on a quiet day and a dead one look identical from outside."""

from __future__ import annotations

from conftest import captured, make_event
from friday.conversation import ConversationId
from friday.liveness import Heartbeat
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
