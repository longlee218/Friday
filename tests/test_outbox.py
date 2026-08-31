"""Ticket 12 — outbound as a row.

Deciding what to say and knowing where to put it are different jobs. A workflow
produces something to send; the Outbox delivers it. Nothing else calls a
provider.
"""

from __future__ import annotations

import pytest

from conftest import make_event
from friday.conversation import ConversationId
from friday.outbox import Kind, Outbox

WATCHED = ConversationId("fake", "watched")


class Sender:
    """Stands in for one outbound identity."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str | None]] = []

    async def send(self, conversation, text, *, reply_to=None) -> None:
        self.sent.append((conversation.target_id, text, reply_to))


class Refusing(Sender):
    def __init__(self, error="discord said no") -> None:
        super().__init__()
        self._error = error

    async def send(self, conversation, text, *, reply_to=None) -> None:
        raise RuntimeError(self._error)


async def task(db, *, state="pending", approved=False):
    created = await db.create_task(
        conversation=WATCHED, type="api_issue", state=state,
        confidence=0.9, params={"summary": "s"},
    )
    if approved:
        await db.approve_task(created.id, by="operator")
    return created


def outbox(db, sender, **kw):
    kw.setdefault("max_attempts", 3)
    return Outbox(db=db, senders={"discord_user": sender}, **kw)


async def test_a_queued_ask_is_delivered(db):
    opened = await task(db)
    await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user", text="which environment?", reply_to="m1",
    )
    sender = Sender()

    await outbox(db, sender).run_once()

    assert sender.sent == [("watched", "which environment?", "m1")]
    assert [r.state for r in await db.outbound()] == ["sent"]


async def test_a_reply_waits_for_its_task_to_be_approved(db):
    """The guard is the query, not a check each caller has to remember."""
    opened = await task(db)
    await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.REPLY,
        sender="discord_user", text="here is your answer",
    )
    sender = Sender()

    await outbox(db, sender).run_once()

    assert sender.sent == []
    assert [r.state for r in await db.outbound()] == ["queued"]


async def test_an_approved_reply_goes_out(db):
    opened = await task(db, approved=True)
    await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.REPLY,
        sender="discord_user", text="here is your answer",
    )
    sender = Sender()

    await outbox(db, sender).run_once()

    assert [text for _, text, _ in sender.sent] == ["here is your answer"]


async def test_asking_for_missing_details_never_waits_for_approval(db):
    """It completes the task's own required parameters. It is not the agent
    speaking for the operator, so there is nothing to approve."""
    assert Kind.ASK_FOR_DETAILS.needs_approval is False
    assert Kind.APPROVAL_CARD.needs_approval is False
    assert Kind.REPLY.needs_approval is True


async def test_a_row_is_never_sent_twice(db):
    opened = await task(db)
    await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user", text="which environment?",
    )
    sender = Sender()
    box = outbox(db, sender)

    await box.run_once()
    await box.run_once()

    assert len(sender.sent) == 1


async def test_a_failed_send_is_retried_rather_than_dropped(db):
    opened = await task(db)
    await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user", text="which environment?",
    )

    await outbox(db, Refusing(), backoff_seconds=0).run_once()

    (row,) = await db.outbound()
    assert row.state == "queued"
    assert row.attempts == 1
    assert "discord said no" in row.last_error


async def test_backoff_holds_a_row_back_before_the_next_attempt(db):
    """Retrying a rate-limited send immediately is how a rate limit becomes a
    ban."""
    opened = await task(db)
    await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user", text="which environment?",
    )
    box = outbox(db, Refusing(), backoff_seconds=60)

    await box.run_once()
    assert await box.run_once() == []          # held back
    assert (await db.outbound())[0].attempts == 1


async def test_giving_up_records_why_and_asks_a_human(db):
    """Never silence: something a person has to send by hand is work, and it
    has to look like work."""
    opened = await task(db)
    await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user", text="which environment?",
    )
    box = outbox(db, Refusing(), max_attempts=2, backoff_seconds=0)

    await box.run_once()
    await box.run_once()

    (row,) = await db.outbound()
    assert row.state == "failed"
    assert "discord said no" in row.last_error
    assert (await db.tasks())[0].state == "needs_human"


async def test_an_unknown_sender_is_given_up_on_immediately(db):
    """Retrying a name that does not exist cannot ever succeed."""
    opened = await task(db)
    await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.ASK_FOR_DETAILS,
        sender="carrier_pigeon", text="which environment?",
    )

    await outbox(db, Sender()).run_once()

    assert (await db.outbound())[0].state == "failed"


async def test_sending_by_hand_is_recorded_apart_from_abandoning(db):
    opened = await task(db)
    queued = await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user", text="which environment?",
    )
    await db.fail_outbound(queued.id, "gave up")

    await db.mark_outbound_sent_manually(queued.id)

    assert (await db.outbound())[0].state == "sent_manually"
