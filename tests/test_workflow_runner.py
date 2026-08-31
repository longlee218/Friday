"""Acting on tasks. The only reply allowed out without review is the request
for missing details: it is the same question every time, and being wrong about
it costs someone one unnecessary question."""

from __future__ import annotations

import pytest

from friday.models import Task
from friday.conversation import ConversationId
from friday.workflows.runner import ASKED, WorkflowRunner


class RecordingProvider:
    name = "fake"

    def __init__(self):
        self.sent = []

    async def send(self, conversation, text: str) -> None:
        self.sent.append((conversation.target_id, text))


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
    provider = RecordingProvider()

    acted = await WorkflowRunner(db=db, provider=provider, auto_ask=True).run_once()

    assert len(provider.sent) == 1
    assert provider.sent[0][0] == "watched"
    assert acted[0].state == ASKED


async def test_nothing_is_sent_when_auto_asking_is_off(db):
    await make_task(db)
    provider = RecordingProvider()

    await WorkflowRunner(db=db, provider=provider, auto_ask=False).run_once()

    assert provider.sent == []
    assert (await db.tasks())[0].state == "needs_human"


async def test_a_task_is_acted_on_only_once(db):
    await make_task(db)
    provider = RecordingProvider()
    runner = WorkflowRunner(db=db, provider=provider, auto_ask=True)

    await runner.run_once()
    await runner.run_once()

    assert len(provider.sent) == 1


async def test_a_report_that_can_be_traced_waits_for_a_human(db):
    """Tracing is not built. Parking is honest; replying would not be."""
    await make_task(db, correlation_id="7f3a91c2")
    provider = RecordingProvider()

    await WorkflowRunner(db=db, provider=provider, auto_ask=True).run_once()

    assert provider.sent == []
    assert (await db.tasks())[0].state == "needs_human"


async def test_types_without_a_workflow_wait_for_a_human(db):
    await db.create_task(conversation=ConversationId("fake", "watched"), type="doc_question",
                         state="pending", confidence=0.9, params={"question": "?"})
    provider = RecordingProvider()

    await WorkflowRunner(db=db, provider=provider, auto_ask=True).run_once()

    assert provider.sent == []
    assert (await db.tasks())[0].state == "needs_human"
