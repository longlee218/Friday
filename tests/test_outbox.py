"""Ticket 12 — outbound as a row.

Deciding what to say and knowing where to put it are different jobs. A workflow
produces something to send; the Outbox delivers it. Nothing else calls a
provider.
"""

from __future__ import annotations

import pytest

from conftest import make_event
from friday.kernel.domain.conversation import ConversationId
from friday.kernel.domain.models import Outbound
from friday.kernel.domain.states import OutboundState, TaskState
from friday.kernel.outbox import Kind, Outbox, record_decision

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


async def task(db, *, state="pending"):
    return await db.create_task(
        conversation=WATCHED, type="devops.api_issue", state=state,
        confidence=0.9, params={"summary": "s"},
    )


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


async def test_a_reply_waits_to_be_approved(db):
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
    opened = await task(db)
    row = await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.REPLY,
        sender="discord_user", text="here is your answer",
    )
    await db.approve_outbound(row.id, by="operator")
    sender = Sender()

    await outbox(db, sender).run_once()

    assert [text for _, text, _ in sender.sent] == ["here is your answer"]


async def test_asking_for_missing_details_never_waits_for_approval(db):
    """It completes the task's own required parameters. It is not the agent
    speaking for the operator, so there is nothing to approve."""
    assert Kind.ASK_FOR_DETAILS.needs_approval is False
    assert Kind.APPROVAL_CARD.needs_approval is False
    assert Kind.REPLY.needs_approval is True


# ── Ticket 03: only the operator may release a reply ──────────────────────────
OPERATOR = 100000000000000001
STRANGER = 999


async def _pending_reply(db):
    opened = await task(db)
    return await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.REPLY,
        sender="discord_user", text="here is your answer",
    )


async def test_the_operator_approving_makes_the_reply_sendable(db):
    row = await _pending_reply(db)

    applied = await record_decision(
        db, outbound_id=row.id, approved=True,
        by="operator", by_id=OPERATOR, operator_id=OPERATOR,
    )

    assert applied is True
    assert [r.id for r in await db.sendable_outbound()] == [row.id]


async def test_a_non_operator_decider_cannot_approve(db):
    """The reply speaks in the operator's name, so only they may release it —
    checked here against operator_id, not trusted from whoever pressed the
    button."""
    row = await _pending_reply(db)

    applied = await record_decision(
        db, outbound_id=row.id, approved=True,
        by="a stranger", by_id=STRANGER, operator_id=OPERATOR,
    )

    assert applied is False
    assert await db.sendable_outbound() == [], "the reply must not become sendable"


async def test_the_operator_rejecting_sends_the_task_to_a_human(db):
    row = await _pending_reply(db)

    applied = await record_decision(
        db, outbound_id=row.id, approved=False,
        by="operator", by_id=OPERATOR, operator_id=OPERATOR,
    )

    assert applied is True
    assert (await db.tasks())[0].state == "needs_human"


async def test_a_non_operator_decider_cannot_reject_either(db):
    row = await _pending_reply(db)

    applied = await record_decision(
        db, outbound_id=row.id, approved=False,
        by="a stranger", by_id=STRANGER, operator_id=OPERATOR,
    )

    assert applied is False
    assert (await db.tasks())[0].state != "needs_human"


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


async def test_approving_a_reply_releases_it(db):
    """The whole point of the predicate: approval is recorded on the row, and
    the reply becomes sendable without anything having to remember it was
    waiting."""
    opened = await task(db)
    row = await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.REPLY,
        sender="discord_user", text="cho anh xin correlationId",
    )
    sender = Sender()

    assert await outbox(db, sender).run_once() == []

    await db.approve_outbound(row.id, by="longle_")
    await outbox(db, sender).run_once()

    assert [text for _, text, _ in sender.sent] == ["cho anh xin correlationId"]


async def test_approving_one_reply_does_not_approve_the_next(db):
    """Finding A: approval was a fact about the task, so once "đang xử lý"
    was approved every later reply on that task went out unread — including
    the one that asserts a cause. The operator approved a row, not a task."""
    opened = await task(db)
    first = await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.REPLY,
        sender="discord_user", text="đang xử lý",
    )
    await db.approve_outbound(first.id, by="longle_")
    await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.REPLY,
        sender="discord_user", text="nguyên nhân là cache đầy",
    )

    assert [r.text for r in await db.sendable_outbound()] == ["đang xử lý"]


async def test_an_answer_the_conversation_has_moved_past_is_not_posted(db):
    """A draft is written against a conversation that keeps moving, and the
    approval arrives minutes or hours later. Answering a question that has since
    been withdrawn, corrected, or answered by someone else is worse than saying
    nothing."""
    from conftest import make_event

    opened = await task(db)
    await db.record_message(make_event(message_id="10", text="api is broken"))
    row = await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.REPLY,
        sender="discord_user", text="here is your answer", reply_to="10",
    )
    await db.approve_outbound(row.id, by="operator")
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

    from friday.kernel.domain.models import InboundEvent

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
        make_event(message_id=message_id), task.id, decision={"type": "devops.api_issue"}
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


# ── Ticket 07: the outbox as a crash-safe delivery ───────────────────────────


class Idempotent(Sender):
    """A channel that dedupes a repeated send from an idempotency key, so a
    resume may safely re-send it."""

    supports_idempotency = True

    def __init__(self) -> None:
        super().__init__()
        self.keys: list[str | None] = []

    async def send(self, row, *, idempotency_key=None) -> str:
        self.keys.append(idempotency_key)
        return await super().send(row)


async def test_a_policy_approved_kind_is_frozen_at_enqueue(db):
    """A kind that needs no approval is released by policy at enqueue, and its
    payload is hashed there — so the dispatch check has something to hold it
    against, the same as a reply gets at approval."""
    opened = await task(db)
    await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user", text="which environment?",
    )

    (row,) = await db.outbound()
    assert row.approved_payload_hash is not None


async def test_a_reply_edited_after_approval_is_not_sent(db):
    """The frozen hash is the guard: an approved reply whose text changed no
    longer says what the operator released, so the approval is void and the
    reply goes to a person instead of out."""
    opened = await task(db)
    row = await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.REPLY,
        sender="discord_user", text="here is your answer",
    )
    await db.approve_outbound(row.id, by="operator")
    # Something changed the message after it was approved.
    await db._set_outbound(row.id, text="a different answer entirely")
    sender = Sender()

    await outbox(db, sender).run_once()

    assert sender.sent == []
    assert (await db.outbound())[0].state == "failed"
    assert (await db.tasks())[0].state == "needs_human"


async def test_a_send_interrupted_mid_call_becomes_delivery_unknown(db):
    """A row found `dispatching` was interrupted between the channel call and
    the record of it. On a channel that cannot dedupe, the outcome is unknown,
    so it goes to the operator — never an automatic retry that might double-post."""
    opened = await task(db)
    row = await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user", text="which environment?",
    )
    # The marker a crash mid-send would leave behind.
    await db.mark_outbound_dispatching(row.id)
    sender = Sender()

    outcome = await outbox(db, sender).deliver_once(row.id)

    assert outcome == "delivery_unknown"
    assert sender.sent == [], "an unknown send is never retried automatically"
    assert (await db.outbound())[0].state == "delivery_unknown"
    assert (await db.tasks())[0].state == "needs_human"


async def test_an_idempotent_channel_resends_a_dispatching_row(db):
    """A channel that dedupes turns a crash mid-send into a safe re-send: the
    same idempotency key means the channel sees the two sends as one, so the
    row is delivered rather than handed to the operator."""
    opened = await task(db)
    row = await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user", text="which environment?",
    )
    await db.mark_outbound_dispatching(row.id)
    sender = Idempotent()

    outcome = await outbox(db, sender).deliver_once(row.id)

    assert outcome == "sent"
    assert sender.keys == [f"outbox-{row.id}"], "the key is the row, stable across resends"
    assert (await db.outbound())[0].state == "sent"


async def test_the_delivery_step_marks_dispatching_before_it_calls_the_channel(db):
    """`dispatching` is written before the send — the ordering the whole crash
    story rests on. A sender that reads the row's state as it is called sees it."""
    opened = await task(db)
    row = await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user", text="which environment?",
    )
    seen = {}

    class Peeking(Sender):
        async def send(self, r) -> str:
            seen["state"] = (await db.outbound_row(r.id)).state
            return await super().send(r)

    await outbox(db, Peeking()).deliver_once(row.id)

    assert seen["state"] == "dispatching"


async def test_a_row_already_resolved_is_not_delivered_again(db):
    """A resumed delivery whose row another pass already finished with is a
    no-op — the guard that lets DBOS re-run the step safely."""
    opened = await task(db)
    row = await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user", text="which environment?",
    )
    await db.mark_outbound_delivery_unknown(row.id, "already handled")
    sender = Sender()

    outcome = await outbox(db, sender).deliver_once(row.id)

    assert outcome == "delivery_unknown"
    assert sender.sent == []


async def test_a_reply_approved_before_the_hash_existed_is_not_sent(db):
    """A row approved before this ticket carries no frozen hash. Fail-closed: an
    unhashable-against approval is treated as void, so the row goes to a person
    to re-approve rather than out unchecked."""
    opened = await task(db)
    row = await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind=Kind.REPLY,
        sender="discord_user", text="here is your answer",
    )
    await db.approve_outbound(row.id, by="operator")
    # A legacy row: approved, but with no hash the migration could backfill.
    await db._set_outbound(row.id, approved_payload_hash=None)
    sender = Sender()

    await outbox(db, sender).run_once()

    assert sender.sent == []
    assert (await db.outbound())[0].state == "failed"
    assert (await db.tasks())[0].state == "needs_human"


def test_the_two_lists_of_what_needs_approval_cannot_drift():
    """**Which kinds wait is answered twice**, and only one of the two is a
    rule anybody reads. `Kind.needs_approval` is where the reasoning lives;
    `friday.store.db._NEEDS_APPROVAL` is the `WHERE` clause that actually
    holds a row back, and it is a tuple of bare strings sitting in another
    module.

    Nothing connected them until this. Adding a kind that needs approval and
    forgetting the tuple does not fail, does not warn, and does not look
    wrong in review — it sends the message. Two kinds were added on
    2026-09-22 and it was luck that neither needed approval.
    """
    import friday.store.db as store

    from friday.kernel.outbox import Kind

    assert {k.value for k in Kind if k.needs_approval} == set(store._NEEDS_APPROVAL)


class SelfApprovingStore:
    """A store that hands the outbox a reply it "approved" itself — no operator
    decision, no frozen `approved_payload_hash`. Ticket 16: the approval
    invariant lives in the kernel outbox, not in whatever the store returns, so
    even a store that self-approves cannot get an unapproved reply sent.
    """

    def __init__(self, row: Outbound) -> None:
        self._row = row
        self.failed: list[tuple[int, str]] = []
        self.moved: list[tuple[int, TaskState]] = []

    async def sendable_outbound(self, limit: int) -> list[Outbound]:
        return [self._row]  # "sendable" by the store's own say-so

    async def outbound_row(self, outbound_id: int) -> Outbound:
        return self._row

    async def fail_outbound(self, outbound_id: int, reason: str) -> None:
        self.failed.append((outbound_id, reason))

    async def move_task(self, task_id: int, state: TaskState) -> None:
        self.moved.append((task_id, state))


async def test_a_store_that_approves_a_reply_on_its_own_is_ignored():
    """The invariant is the outbox's, not the store's (ticket 16, box 4). A
    reply that reaches delivery with no frozen approval behind it is stopped and
    handed to a human — the channel is never called — however sendable the store
    claimed it was."""
    reply = Outbound(
        id=1, task_id=7, conversation=WATCHED, kind=Kind.REPLY, sender="discord_user",
        text="the cause is a null tx", reply_to="m1", state=OutboundState.QUEUED,
        attempts=0, last_error=None, approves=None, approved_payload_hash=None,
    )
    store = SelfApprovingStore(reply)
    sender = Sender()

    attempted = await Outbox(db=store, senders={"discord_user": sender}).run_once()

    assert [r.id for r in attempted] == [1]
    assert sender.sent == [], "an unapproved reply must never reach the channel"
    assert store.failed and store.failed[0][0] == 1
    assert store.moved == [(7, TaskState.NEEDS_HUMAN)]
