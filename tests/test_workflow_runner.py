"""Acting on tasks. The only reply allowed out without review is the request
for missing details: it is the same question every time, and being wrong about
it costs someone one unnecessary question."""

from __future__ import annotations

import pytest

from friday.models import Task
from friday.conversation import ConversationId
from friday.workflows.runner import ASKED, WorkflowRunner
from friday.tasks import TaskState




async def make_task(db, **params):
    return await db.create_task(
        conversation=ConversationId("fake", "watched"),
        type="api_issue",
        state="pending",
        confidence=0.9,
        params={"summary": "checkout 500", "environment": None,
                "correlation_id": None, "curl": None, **params},
    )


async def test_a_report_missing_details_is_asked_about(db):
    await make_task(db)

    acted = await WorkflowRunner(db=db, auto_ask=True).run_once()

    (queued,) = await db.outbound()
    assert queued.kind == "ask_for_details"
    assert queued.sender == "discord_user"
    assert queued.conversation.target_id == "watched"
    assert acted[0].state == ASKED


async def test_nothing_is_sent_when_auto_asking_is_off(db):
    await make_task(db)

    await WorkflowRunner(db=db, auto_ask=False).run_once()

    assert await db.outbound() == []
    assert (await db.tasks())[0].state == "needs_human"


async def test_a_task_is_acted_on_only_once(db):
    await make_task(db)
    runner = WorkflowRunner(db=db, auto_ask=True)

    await runner.run_once()
    await runner.run_once()

    assert len(await db.outbound()) == 1


async def test_a_report_that_can_be_traced_waits_for_a_human(db):
    """Tracing is not built. Parking is honest; replying would not be."""
    await make_task(db, correlation_id="7f3a91c2")

    await WorkflowRunner(db=db, auto_ask=True).run_once()

    assert await db.outbound() == []
    assert (await db.tasks())[0].state == "needs_human"


async def test_types_without_a_workflow_wait_for_a_human(db):
    await db.create_task(conversation=ConversationId("fake", "watched"), type="doc_question",
                         state="pending", confidence=0.9, params={"question": "?"})

    await WorkflowRunner(db=db, auto_ask=True).run_once()

    assert await db.outbound() == []
    assert (await db.tasks())[0].state == "needs_human"


async def test_a_task_stops_being_asked_after_a_few_tries(db):
    """Asking forever is how a helpful question becomes noise. After the bound
    it becomes a human's problem, which is what a human is for."""
    opened = await make_task(db)
    runner = WorkflowRunner(db=db, auto_ask=True, max_asks=2)

    for _ in range(3):
        await db.move_task(opened.id, TaskState.PENDING)
        await runner.run_once()

    assert len(await db.outbound()) == 2
    assert (await db.tasks())[0].state == "needs_human"


class StubResponder:
    def __init__(self, text=None):
        self._text = text
        self.asked: list[str] = []

    async def draft(self, *, asking, context=(), tone=(), calls=None):
        from friday.responder import Draft

        self.asked.append(asking)
        return Draft(self._text) if self._text else None


async def test_a_drafted_reply_waits_for_approval(db):
    """The template goes out unreviewed because it is the same sentence every
    time. A model writing it makes that untrue, so a draft is a reply."""
    await make_task(db)
    responder = StubResponder("cho anh xin cái correlationId với")

    await WorkflowRunner(db=db, auto_ask=True, responder=responder).run_once()

    reply, card = await db.outbound()
    assert reply.kind == "reply"
    assert reply.text == "cho anh xin cái correlationId với"
    # Only the question is sendable. The reply itself waits to be answered.
    assert [r.kind for r in await db.sendable_outbound()] == ["approval_card"]


async def test_the_responder_is_told_what_to_say(db):
    await make_task(db)
    responder = StubResponder("ok")

    await WorkflowRunner(db=db, auto_ask=True, responder=responder).run_once()

    assert "correlationId" in responder.asked[0]


async def test_the_template_still_goes_out_when_the_responder_cannot(db):
    """Never a wrong reply in the operator's name; never silence either."""
    await make_task(db)

    await WorkflowRunner(db=db, auto_ask=True, responder=StubResponder(None)).run_once()

    (queued,) = await db.outbound()
    assert queued.kind == "ask_for_details"
    assert await db.sendable_outbound() != []


async def test_without_a_responder_nothing_changes(db):
    await make_task(db)

    await WorkflowRunner(db=db, auto_ask=True).run_once()

    assert (await db.outbound())[0].kind == "ask_for_details"


async def test_a_draft_comes_with_a_card_asking_about_it(db):
    """A draft nobody was asked about waits forever. The card is itself an
    outbound row, so a card that fails to send is visible rather than silent."""
    await make_task(db)

    await WorkflowRunner(
        db=db, auto_ask=True, responder=StubResponder("cho anh xin correlationId")
    ).run_once()

    kinds = [r.kind for r in await db.outbound()]
    assert kinds == ["reply", "approval_card"]

    card = (await db.outbound())[1]
    assert card.sender == "discord_bot"
    assert card.text == "cho anh xin correlationId"
    assert await db.sendable_outbound() == [card]  # the card goes; the reply waits


async def test_no_card_when_there_is_nothing_to_approve(db):
    await make_task(db)

    await WorkflowRunner(db=db, auto_ask=True).run_once()

    assert [r.kind for r in await db.outbound()] == ["ask_for_details"]


async def test_a_task_waiting_on_approval_is_in_review(db):
    """`waiting_for_details` means waiting on the reporter. This is waiting on
    the operator, which is a different thing and its own column on the board."""
    await make_task(db)

    acted = await WorkflowRunner(
        db=db, auto_ask=True, responder=StubResponder("cho anh xin correlationId")
    ).run_once()

    assert acted[0].state == "review"


async def test_a_task_waiting_on_the_reporter_still_is(db):
    await make_task(db)

    acted = await WorkflowRunner(db=db, auto_ask=True).run_once()

    assert acted[0].state == ASKED
