"""Ticket 04 slice 4 — applying triage decisions.

Triage decides; this applies. Kept apart so the decision is testable without a
database and the application is testable without a model.
"""

from __future__ import annotations

from conftest import captured, make_event
from friday.triage import (
    AccessRequestParams,
    ApiIssueParams,
    Decided,
    NeedsHuman,
    SkipParams,
)
from friday.triage.runner import TriageRunner
from friday.tasks import TaskState


class StubTriage:
    """Returns preset outcomes. The runner's seam is the decision, not the model."""

    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.seen = []

    async def decide(self, event, *, context=(), calls=None):
        self.seen.append(event)
        return self._outcomes.pop(0) if self._outcomes else NeedsHuman("no script")


def api_issue(confidence=0.9, **kw):
    params = ApiIssueParams(summary="checkout 500", **kw)
    return Decided(type="api_issue", confidence=confidence, params=params)


def runner(db, triage, threshold=0.7):
    return TriageRunner(db=db, triage=triage, confidence_threshold=threshold)


async def test_a_confident_decision_becomes_a_task(inbox, provider, db):
    provider.emit(make_event(message_id="10"))
    await captured(inbox)

    created = await runner(db, StubTriage(api_issue())).run_once()

    assert [t.type for t in created] == ["api_issue"]
    stored = await db.tasks()
    assert stored[0].params["summary"] == "checkout 500"
    assert stored[0].state == "pending"


async def test_a_skip_creates_no_task(inbox, provider, db):
    provider.emit(make_event(message_id="10"))
    await captured(inbox)
    skip = Decided(type="skip", confidence=0.99, params=SkipParams("banter"))

    assert await runner(db, StubTriage(skip)).run_once() == []
    assert await db.tasks() == []


async def test_an_event_is_triaged_only_once(inbox, provider, db):
    provider.emit(make_event(message_id="10"))
    await captured(inbox)
    triage = StubTriage(api_issue())
    r = runner(db, triage)

    await r.run_once()
    await r.run_once()

    assert len(triage.seen) == 1
    assert len(await db.tasks()) == 1


async def test_low_confidence_asks_for_a_human_instead_of_guessing(
    inbox, provider, db
):
    provider.emit(make_event(message_id="10"))
    await captured(inbox)

    created = await runner(db, StubTriage(api_issue(confidence=0.3))).run_once()

    assert created[0].state == "needs_human"


async def test_an_undecidable_message_still_becomes_work(inbox, provider, db):
    """Never-drop: silence is indistinguishable from working correctly."""
    provider.emit(make_event(message_id="10"))
    await captured(inbox)

    created = await runner(db, StubTriage(NeedsHuman("model exploded"))).run_once()

    assert created[0].state == "needs_human"
    assert "model exploded" in created[0].params["reason"]


async def test_a_follow_up_updates_the_open_task_rather_than_opening_a_second(
    inbox, provider, db
):
    provider.emit(make_event(message_id="10"))
    provider.emit(make_event(message_id="20", text="still broken"))
    await captured(inbox)

    r = runner(db, StubTriage(api_issue(), api_issue()))
    await r.run_once()

    assert len(await db.tasks()) == 1


async def test_a_follow_up_of_a_different_type_asks_for_a_human(inbox, provider, db):
    """A bug report that turns into something else should not be relabelled
    silently."""
    provider.emit(make_event(message_id="10"))
    provider.emit(make_event(message_id="20", text="actually give me repo access"))
    await captured(inbox)
    access = Decided(
        type="access_request",
        confidence=0.9,
        params=AccessRequestParams("repo", "write", "needs access"),
    )

    r = runner(db, StubTriage(api_issue(), access))
    await r.run_once()

    tasks = await db.tasks()
    assert len(tasks) == 1
    assert tasks[0].state == "needs_human"


async def test_triage_reads_the_conversations_context(inbox, provider, db):
    provider.emit_recent("watched", make_event(message_id="1", text="deploy went out"))
    provider.emit(make_event(message_id="10"))
    await captured(inbox)
    triage = StubTriage(api_issue())

    await runner(db, triage).run_once()

    assert triage.seen  # and it was given the conversation, see runner


async def test_a_follow_up_merges_new_details_into_the_open_task(inbox, provider, db):
    """The answer to "which environment?" arrives as an ordinary message.

    If it does not reach the task, the task waits forever for something it has
    already been told.
    """
    provider.emit(make_event(message_id="10"))
    await captured(inbox)
    await runner(db, StubTriage(api_issue())).run_once()
    await db.move_task((await db.tasks())[0].id, TaskState.WAITING_FOR_DETAILS)

    provider.emit(make_event(message_id="20", text="prod, correlationId abc-123"))
    await captured(inbox)
    answered = api_issue(environment="production", correlation_id="abc-123")
    await runner(db, StubTriage(answered)).run_once()

    task = (await db.tasks())[0]
    assert task.params["correlation_id"] == "abc-123"
    assert task.params["environment"] == "production"
    assert task.state == "pending"  # re-planned now that it can be traced

async def test_a_skip_is_still_recorded_as_a_decision(inbox, provider, db):
    """A skip creates no task, so without this the decision leaves no trace and
    the confidence threshold can never be checked against real messages."""
    provider.emit(make_event(message_id="10"))
    await captured(inbox)
    skip = Decided(type="skip", confidence=0.95, params=SkipParams("banter"))

    await runner(db, StubTriage(skip)).run_once()

    (decision,) = await db.decisions()
    assert decision["type"] == "skip"
    assert decision["confidence"] == 0.95
    assert decision["params"]["reason"] == "banter"
    assert decision["task_id"] is None


async def test_an_opened_task_records_its_decision_too(inbox, provider, db):
    provider.emit(make_event(message_id="10"))
    await captured(inbox)

    created = await runner(db, StubTriage(api_issue())).run_once()

    (decision,) = await db.decisions()
    assert decision["type"] == "api_issue"
    assert decision["confidence"] == 0.9
    assert decision["task_id"] == created[0].id


async def test_a_follow_ups_decision_is_recorded_separately(inbox, provider, db):
    """Follow-ups are absorbed into the open task. The decision behind them is
    still evidence about the classifier."""
    provider.emit(make_event(message_id="10"))
    provider.emit(make_event(message_id="20", text="still broken"))
    await captured(inbox)

    await runner(db, StubTriage(api_issue(), api_issue(confidence=0.55))).run_once()

    assert [d["confidence"] for d in await db.decisions()] == [0.9, 0.55]


async def test_an_escalation_is_recorded_as_a_decision(inbox, provider, db):
    provider.emit(make_event(message_id="10"))
    await captured(inbox)

    await runner(db, StubTriage(NeedsHuman("model exploded"))).run_once()

    (decision,) = await db.decisions()
    assert decision["type"] == "needs_human"
    assert "model exploded" in decision["params"]["reason"]


async def test_a_follow_up_without_the_details_asks_again(inbox, provider, db):
    """A task waiting for a correlationId that gets another message without one
    still needs it. Staying silent leaves the reporter thinking they were
    heard."""
    provider.emit(make_event(message_id="10"))
    await captured(inbox)
    await runner(db, StubTriage(api_issue())).run_once()
    await db.move_task((await db.tasks())[0].id, TaskState.WAITING_FOR_DETAILS)

    provider.emit(make_event(message_id="20", text="it is still slow"))
    await captured(inbox)
    await runner(db, StubTriage(api_issue())).run_once()

    assert (await db.tasks())[0].state == "pending"  # the workflow will re-ask
