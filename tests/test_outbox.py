"""Ticket 12 — outbound as a row.

Deciding what to say and knowing where to put it are different jobs. A workflow
produces something to send; the Outbox delivers it. Nothing else calls a
provider.
"""

from __future__ import annotations

import pytest

from conftest import make_event
from friday.domain.conversation import ConversationId
from friday.outbox import Kind, Outbox

WATCHED = ConversationId("fake", "watched")


class Sender:
    """Stands in for one outbound identity."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str | None]] = []

    async def send(self, row) -> str:
        self.sent.append((row.conversation.target_id, row.text, row.reply_to))
        return f"sent-{len(self.sent)}"


class Refusing(Sender):
    def __init__(self, error="discord said no") -> None:
        super().__init__()
        self._error = error

    async def send(self, row) -> None:
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


async def test_approving_a_task_releases_its_reply(db):
    """The whole point of the join: approval is recorded on the task, and the
    reply becomes sendable without anything having to remember it was waiting."""
    opened = await task(db)
    await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.REPLY,
        sender="discord_user", text="cho anh xin correlationId",
    )
    sender = Sender()

    assert await outbox(db, sender).run_once() == []

    await db.approve_task(opened.id, by="longle_")
    await outbox(db, sender).run_once()

    assert [text for _, text, _ in sender.sent] == ["cho anh xin correlationId"]


async def test_an_answer_the_conversation_has_moved_past_is_not_posted(db):
    """A draft is written against a conversation that keeps moving, and the
    approval arrives minutes or hours later. Answering a question that has since
    been withdrawn, corrected, or answered by someone else is worse than saying
    nothing."""
    from conftest import make_event

    opened = await task(db, approved=True)
    await db.record_message(make_event(message_id="10", text="api is broken"))
    row = await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.REPLY,
        sender="discord_user", text="here is your answer", reply_to="10",
    )
    # They said something else while it waited to be approved.
    await db.record_message(make_event(message_id="20", text="never mind, fixed it"))
    sender = Sender()

    await outbox(db, sender).run_once()

    assert sender.sent == []
    assert (await db.outbound())[0].state == "failed"
    assert (await db.tasks())[0].state == "needs_human"


async def test_an_ask_is_not_held_back_by_a_newer_message(db):
    """Asking for a correlationId is still worth asking after they have said
    something else. Only an *answer* goes stale."""
    from conftest import make_event

    opened = await task(db)
    await db.record_message(make_event(message_id="10", text="api is broken"))
    await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user", text="which environment?", reply_to="10",
    )
    await db.record_message(make_event(message_id="20", text="anyone?"))
    sender = Sender()

    await outbox(db, sender).run_once()

    assert [t for _, t, _ in sender.sent] == ["which environment?"]


# --- what we asked and have not been answered (ticket 05) ----------------


async def _asked(db, task, text, *, sent_message_id):
    """Queue a request for details and mark it sent, the way the outbox loop
    does — a queued question has not been asked yet, so only a sent one can be
    unanswered."""
    row = await db.queue_outbound(
        task_id=task.id,
        conversation=task.conversation,
        kind="ask_for_details",
        sender="discord_user",
        text=text,
    )
    await db.mark_outbound_sent(row.id, sent_message_id=sent_message_id)
    return row


async def _replied(db, task, message_id, text, *, to, at=None):
    """The reporter answering something we sent, at a time after we sent it.

    Its own helper rather than `tests/test_pool._said`, for a reason worth
    saying: that one stamps messages relative to a fixed date in the past
    while `mark_outbound_sent` stamps `sent_at` from the real clock, so a
    reply built with it always predates the question it answers. "Answered"
    is a question of ordering, so the ordering has to be constructible.
    """
    from datetime import datetime, timezone

    from friday.domain.models import InboundEvent

    await db.record_message(
        InboundEvent(
            provider="fake",
            provider_message_id=message_id,
            channel_id="watched",
            thread_id=None,
            author_id="u-reporter",
            author_name="dana",
            text=text,
            created_at=at or datetime.now(timezone.utc),
            mention_type=None,
            reply_to=to,
        )
    )


async def _opened_by(db, task, message_id="m1"):
    """Link an opening message to the task, so the store can tell who the
    reporter is."""
    from conftest import make_event
    from tests.test_pool import _said

    await _said(db, message_id, "@Lee API lỗi", secs=0, mention=True)
    await db.mark_triaged(
        make_event(message_id=message_id), task.id, decision={"type": "api_issue"}
    )


async def test_a_conversation_that_asked_nothing_has_nothing_outstanding(db):
    from tests.test_pool import make_task

    task = await make_task(db)

    assert await db.unanswered_questions(task.id) == ()


async def test_a_question_nobody_answered_is_outstanding(db):
    """The recorded failure: the system asked for an environment, the reporter
    did not answer, and nothing in the system knew it was waiting — so the
    next pass was free to ask again."""
    from tests.test_pool import make_task

    task = await make_task(db)
    await _opened_by(db, task)
    await _asked(db, task, "em gửi anh curl với", sent_message_id="out-1")

    assert await db.unanswered_questions(task.id) == ("em gửi anh curl với",)


async def test_a_question_the_reporter_answered_stops_being_outstanding(db):
    """Answered means the reporter replied to what we asked, after we asked
    it. A reply names what it is about; that is the rule everything else here
    uses and it is the rule used here."""
    from tests.test_pool import make_task

    task = await make_task(db)
    await _opened_by(db, task)
    await _asked(db, task, "em gửi anh curl với", sent_message_id="out-1")
    await _replied(db, task, "m2", "curl -X GET /pay", to="out-1")

    assert await db.unanswered_questions(task.id) == ()


async def test_a_second_question_after_an_answer_is_outstanding_again(db):
    """Two asks, one answer in between. The reply answers what was asked
    before it and says nothing about what was asked after."""
    from tests.test_pool import make_task

    task = await make_task(db)
    await _opened_by(db, task)
    # Real waits, because "answered" is a question of ordering and two rows
    # marked sent in the same breath are microseconds apart. A reply has to
    # land between them, so the two moments have to be separable.
    import asyncio

    await _asked(db, task, "em gửi anh curl với", sent_message_id="out-1")
    await asyncio.sleep(0.05)
    await _replied(db, task, "m2", "curl -X GET /pay", to="out-1")
    await asyncio.sleep(0.05)
    await _asked(db, task, "còn environment nào em?", sent_message_id="out-2")

    assert await db.unanswered_questions(task.id) == ("còn environment nào em?",)


async def test_our_own_other_messages_are_not_questions(db):
    """Only a request for details is a question. A reply we sent, or an alert
    about the system, is not something a reporter owes an answer to."""
    from tests.test_pool import make_task

    task = await make_task(db)
    await _opened_by(db, task)
    for kind in ("reply", "proposal"):
        row = await db.queue_outbound(
            task_id=task.id,
            conversation=task.conversation,
            kind=kind,
            sender="discord_user",
            text=f"a {kind}",
        )
        await db.mark_outbound_sent(row.id, sent_message_id=f"out-{kind}")

    assert await db.unanswered_questions(task.id) == ()


async def test_a_question_still_queued_is_not_yet_a_question(db):
    """It has not been asked. Counting it would have the system waiting for an
    answer to something nobody has seen."""
    from tests.test_pool import make_task

    task = await make_task(db)
    await _opened_by(db, task)
    await db.queue_outbound(
        task_id=task.id,
        conversation=task.conversation,
        kind="ask_for_details",
        sender="discord_user",
        text="not sent yet",
    )

    assert await db.unanswered_questions(task.id) == ()
