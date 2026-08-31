"""Acting on tasks. The only reply allowed out without review is the request
for missing details: it is the same question every time, and being wrong about
it costs someone one unnecessary question."""

from __future__ import annotations

import pytest

from friday.models import Task
from friday.conversation import ConversationId
from friday.workflows.runner import ASKED, WorkflowRunner




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
        await db.set_task_state(opened.id, "pending")
        await runner.run_once()

    assert len(await db.outbound()) == 2
    assert (await db.tasks())[0].state == "needs_human"
