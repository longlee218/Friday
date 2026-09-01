"""Acting on tasks. The only reply allowed out without review is the request
for missing details: it is the same question every time, and being wrong about
it costs someone one unnecessary question."""

from __future__ import annotations

import pytest

from friday.domain.models import Task
from datetime import datetime, timezone

from conftest import make_event
from friday.domain.conversation import ConversationId
from friday.workflows.runner import ASKED, WorkflowRunner
from friday.domain.tasks import TaskState




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

    assert [r for r in await db.outbound() if r.sender == "discord_user"] == []
    assert (await db.tasks())[0].state == "needs_human"


async def test_a_task_is_acted_on_only_once(db):
    await make_task(db)
    runner = WorkflowRunner(db=db, auto_ask=True)

    await runner.run_once()
    await runner.run_once()

    assert len([r for r in await db.outbound() if r.sender == "discord_user"]) == 1


async def test_a_report_that_can_be_traced_waits_for_a_human(db):
    """Tracing is not built. Parking is honest; replying would not be."""
    await make_task(db, correlation_id="7f3a91c2-dead-beef-cafe-1234567890ab")

    await WorkflowRunner(db=db, auto_ask=True).run_once()

    assert [r for r in await db.outbound() if r.sender == "discord_user"] == []
    assert (await db.tasks())[0].state == "needs_human"


async def test_types_without_a_workflow_wait_for_a_human(db):
    await db.create_task(conversation=ConversationId("fake", "watched"), type="doc_question",
                         state="pending", confidence=0.9, params={"question": "?"})

    await WorkflowRunner(db=db, auto_ask=True).run_once()

    assert [r for r in await db.outbound() if r.sender == "discord_user"] == []
    assert (await db.tasks())[0].state == "needs_human"


async def test_a_task_stops_being_asked_after_a_few_tries(db):
    """Asking forever is how a helpful question becomes noise. After the bound
    it becomes a human's problem, which is what a human is for."""
    opened = await make_task(db)
    # Debounce off: this is about the bound on asking, not about bursts.
    runner = WorkflowRunner(db=db, auto_ask=True, max_asks=2)

    for _ in range(3):
        await db.move_task(opened.id, TaskState.PENDING)
        await runner.run_once()

    asks = [r for r in await db.outbound() if r.kind == "ask_for_details"]
    assert len(asks) == 2
    assert (await db.tasks())[0].state == "needs_human"


class StubResponder:
    def __init__(self, text=None):
        self._text = text
        self.asked: list[str] = []
        self.given_params: list = []

    async def draft(self, *, asking, params=None, channel_id=None, context=(), tone=(), calls=None):
        from friday.responder import Draft

        self.asked.append(asking)
        self.given_params.append(params)
        return Draft(self._text) if self._text else None


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

    assert [r.kind for r in await db.outbound()] == ["ask_for_details"]


async def test_a_task_waiting_on_the_reporter_still_is(db):
    await make_task(db)

    acted = await WorkflowRunner(db=db, auto_ask=True).run_once()

    assert acted[0].state == ASKED


# ---- who decides what ------------------------------------------------------


async def test_asking_for_details_is_the_agents_own_decision(db):
    """Asking is low-risk whoever phrased it. The operator is interrupted for
    answers and for trouble, not for questions."""
    await make_task(db)

    acted = await WorkflowRunner(
        db=db, auto_ask=True, responder=StubResponder("cho anh xin correlationId")
    ).run_once()

    (queued,) = await db.outbound()
    assert queued.kind == "ask_for_details"
    assert queued.text == "cho anh xin correlationId"
    assert await db.sendable_outbound() == [queued]
    assert acted[0].state == ASKED


async def test_a_task_it_cannot_handle_is_brought_to_the_operator(db):
    """Otherwise it sits in a column nobody is watching."""
    task = await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")  # traceable, unactionable
    runner = WorkflowRunner(db=db, auto_ask=True)

    await runner.run_once()

    assert (await db.tasks())[0].state == "needs_human"
    (card,) = await db.outbound()
    assert card.kind == "help_wanted"
    assert card.sender == "discord_bot"
    assert "abcdef01-2345-6789-abcd-ef0123456789" in card.text


async def test_the_operator_is_told_once(db):
    """A card per poll is a notification that trains you to ignore it."""
    await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
    runner = WorkflowRunner(db=db, auto_ask=True)

    await runner.run_once()
    await runner.run_once()
    await runner.run_once()

    assert [r.kind for r in await db.outbound()] == ["help_wanted"]


# The two debounce tests that lived here are gone with the debounce. A burst is
# absorbed before it reaches this loop now — see the turn tests in
# `test_triage_runner.py` — so there is nothing here to settle.


async def test_an_answer_from_a_workflow_waits_for_approval(db):
    """This is the producer the approval path never had. A workflow that can
    actually answer something says so, and the operator decides whether it goes
    out under their name."""
    from friday.dag import DAG, Node
    from friday.dag.router import EDGE_ROUTER, register_dag
    from friday.workflows import Reply

    async def answers(state, deps):
        return Reply("cache đầy thôi, anh clear rồi nhé")

    # A graph standing in for the real one. This test is about the approval
    # machinery, not about what `api_issue` investigates — it needs a route
    # that produces a `Reply` and nothing more.
    EDGE_ROUTER.pop("api_issue", None)
    register_dag("api_issue", DAG(name="answers", nodes=(Node("answer", answers),)))

    await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
    runner = WorkflowRunner(db=db, auto_ask=True)

    try:
        acted = await runner.run_once()
    finally:
        EDGE_ROUTER.pop("api_issue", None)

    reply, card = await db.outbound()
    assert reply.kind == "reply"
    assert reply.text == "cache đầy thôi, anh clear rồi nhé"
    # Only the question is sendable; the answer waits to be answered.
    assert [r.kind for r in await db.sendable_outbound()] == ["approval_card"]
    assert acted[0].state == "review"


async def test_announcing_costs_the_same_whether_there_are_five_tasks_or_one(db):
    """It ran a count per task, every two seconds, for something that almost
    never has anything to do — twenty-one queries to usually find nothing.

    Both the pause lookup and the already-said lookup are in bulk, so the
    query count does not move with the batch."""
    queries: list[str] = []
    watched = ("tasks_in_state", "dag_pauses", "announced")
    for name in watched:
        original = getattr(db, name)

        def counted(*a, _name=name, _original=original, **kw):
            queries.append(_name)
            return _original(*a, **kw)

        setattr(db, name, counted)

    for _ in range(5):
        task = await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
        await db.move_task(task.id, TaskState.NEEDS_HUMAN)

    await WorkflowRunner(db=db, auto_ask=True).run_once()

    # One `tasks_in_state` for the pending sweep, one for the announcement.
    assert queries == ["tasks_in_state", "tasks_in_state", "dag_pauses", "announced"]
    assert len(await db.outbound()) == 5


async def test_extraction_runs_when_a_message_is_linked(db):
    """End-to-end: a message is recorded, a task is linked to it, the runner
    reads the text, the registered extractor fills fields, the planner gets
    a complete Params and parks. Without the link to original_text, the
    runner has nothing to extract from and the test would only prove the
    read path is plumbed."""
    from dataclasses import dataclass
    from datetime import datetime, timezone

    from sqlalchemy import update as sa_update

    from friday.store import schema
    from friday.config import AgentConfig
    from friday.domain.conversation import ConversationId
    from friday.dag.router import EDGE_ROUTER
    from friday.extraction import _EXTRACTORS, build_extractor
    from friday.agent.harness import Harness
    from friday.domain.models import ApiIssueParams, InboundEvent, MentionType

    class StubResult:
        # The model has to repeat every field, including ones triage already
        # filled, because returning null would cancel triage's value. That is
        # what the extraction prompt instructs and the test mirrors.
        final_output = (
            '{"summary": "checkout 500", '
            '"environment": "production", '
            '"correlation_id": "abcdef01-2345-6789-abcd-ef0123456789", '
            '"curl": null}'
        )

    prompts_seen: list[str] = []

    class StubHarness:
        async def run(self, prompt, *, context=None, calls=None, extra_turns=0):
            prompts_seen.append(prompt)
            return StubResult()

    cfg = AgentConfig(
        name="api_issue_ext",
        api_key="sk-secret",
        base_url="https://example.invalid/v1",
        model="test-model",
    )
    ext = build_extractor(
        params_cls=ApiIssueParams,
        harness=StubHarness(),  # type: ignore[arg-type]
        name="api_issue_ext",
    )
    _EXTRACTORS["api_issue"] = ext
    # Extraction happens inside `plan()`, on the deterministic path. The graph
    # reads the task's stored params directly, so this test is about the
    # extract-merge-validate pipeline and takes the path that has one.
    EDGE_ROUTER.pop("api_issue", None)

    try:
        event = InboundEvent(
            provider="fake",
            provider_message_id="m-ext-1",
            channel_id="watched",
            thread_id=None,
            author_id="u-reporter",
            author_name="reporter",
            text=(
                "production broke at noon, "
                "correlation id abcdef01-2345-6789-abcd-ef0123456789"
            ),
            created_at=datetime.now(timezone.utc),
            mention_type=MentionType.DIRECT,
        )
        await db.record_message(event)
        task = await make_task(db)
        async with db._sessions.begin() as session:
            await session.execute(
                sa_update(schema.Message)
                .where(schema.Message.provider_message_id == "m-ext-1")
                .values(task_id=task.id)
            )

        await WorkflowRunner(db=db, auto_ask=False).run_once()

        # Extraction ran: the harness saw the reporter's text. The exact
        # Park action depends on validate's verdict — what matters here
        # is that the prompt was sent and the message's original text
        # reached the workflow.
        assert prompts_seen, "extractor was never called"
        assert "abcdef01-2345-6789" in prompts_seen[0]
        # And the workflow produced an outbound row of some kind, which is
        # how a task surfaces to a person.
        assert await db.outbound(), "no outbound produced"
    finally:
        _EXTRACTORS.pop("api_issue", None)


async def test_the_responder_is_told_what_this_task_actually_knows(db):
    """Without it the model has only the conversation, and a conversation is a
    whole channel — it may hold another report's correlationId.

    Observed on the real provider: asked to request one, it read the channel,
    found one belonging to a different task, and wrote "ok có correlationId
    rồi, để anh trace thử". False, promising work nobody would do, and sent
    under the operator's name with no approval step.
    """
    from friday.domain.models import ApiIssueParams

    responder = StubResponder("cho anh xin cái correlationId nhé")
    await make_task(db, environment="production")

    await WorkflowRunner(db=db, auto_ask=True, responder=responder).run_once()

    (given,) = responder.given_params
    assert isinstance(given, ApiIssueParams)
    assert given.environment == "production"
    assert given.correlation_id is None, "it must be able to see what is absent"


# --- a burst is one thought sent in three messages ---------------------------


async def _said(db, message_id, text, *, secs, mention=None, author="u-reporter"):
    from datetime import timedelta

    from friday.domain.models import InboundEvent, MentionType

    await db.record_message(
        InboundEvent(
            provider="fake",
            provider_message_id=message_id,
            channel_id="watched",
            thread_id=None,
            author_id=author,
            author_name="dana",
            text=text,
            created_at=BURST_START + timedelta(seconds=secs),
            mention_type=MentionType.DIRECT if mention else None,
        ),
        context_only=not mention,
    )


BURST_START = datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc)


async def test_the_extractor_reads_the_rest_of_the_burst(db):
    """Discord lets you send three messages in five seconds, and people do: a
    mention saying the API is broken, then the curl, then the environment.

    Only the first carries a mention, so only the first is in scope and the
    rest are stored as context with no task on them. The extractor read the
    linked rows, saw one line, and the system asked for a correlationId the
    reporter had sent three seconds earlier.
    """
    task = await make_task(db)
    await _said(db, "m1", "@Lee API lỗi rồi a ơi", secs=0, mention=True)
    await db.mark_triaged(
        make_event(message_id="m1"), task.id, decision={"type": "api_issue"}
    )
    await _said(db, "m2", "curl -X POST /pay trả 500, trên production", secs=3)

    said = await db.original_text_for(task.id)

    assert "API lỗi rồi" in said
    assert "curl -X POST /pay" in said


async def test_somebody_else_talking_is_not_part_of_it(db):
    """Same conversation, different person. A channel is shared."""
    task = await make_task(db)
    await _said(db, "m1", "@Lee API lỗi rồi", secs=0, mention=True)
    await db.mark_triaged(
        make_event(message_id="m1"), task.id, decision={"type": "api_issue"}
    )
    await _said(db, "m2", "trưa nay ăn gì mọi người", secs=3, author="someone-else")

    assert "trưa nay" not in (await db.original_text_for(task.id) or "")


async def test_what_we_posted_is_not_read_back(db):
    """With `capture_own_messages` on, the operator is both the reporter and
    the account, so "same author" would otherwise feed our own questions back
    into the extractor."""
    from friday.outbox import Kind

    task = await make_task(db)
    await _said(db, "m1", "@Lee API lỗi rồi", secs=0, mention=True, author="me")
    await db.mark_triaged(
        make_event(message_id="m1"), task.id, decision={"type": "api_issue"}
    )
    row = await db.queue_outbound(
        task_id=task.id,
        conversation=ConversationId("fake", "watched"),
        kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user",
        text="cho anh xin cái correlationId nhé",
    )
    await db.mark_outbound_sent(row.id, sent_message_id="ours-1")
    await _said(db, "ours-1", "cho anh xin cái correlationId nhé", secs=5, author="me")

    assert "cho anh xin" not in (await db.original_text_for(task.id) or "")


async def test_nothing_said_before_the_report_is_dragged_in(db):
    """Context seeded from before the conversation involved us is context, not
    part of the report."""
    task = await make_task(db)
    await _said(db, "earlier", "hôm qua deploy xong rồi nhé", secs=-3600)
    await _said(db, "m1", "@Lee API lỗi rồi", secs=0, mention=True)
    await db.mark_triaged(
        make_event(message_id="m1"), task.id, decision={"type": "api_issue"}
    )

    assert "hôm qua deploy" not in (await db.original_text_for(task.id) or "")


# --- ticket 37: the operator's own message ends the work ---------------------


async def _operator_said(db, message_id, text, *, reply_to=None, secs=10, author="me"):
    """The watched account typing in the channel. `is_own`, no mention."""
    from datetime import timedelta

    from friday.domain.models import InboundEvent

    await db.record_message(
        InboundEvent(
            provider="fake",
            provider_message_id=message_id,
            channel_id="watched",
            thread_id=None,
            author_id=author,
            author_name="Long",
            text=text,
            created_at=datetime.now(timezone.utc) + timedelta(seconds=secs),
            mention_type=None,
            is_own=True,
            reply_to=reply_to,
        ),
        context_only=True,
    )


async def test_the_operator_answering_closes_the_task_and_withdraws_the_draft(db):
    """A `reply` waits for approval with no expiry. Without this, approving it
    two days later sends an answer that stopped being true when they typed."""
    from friday.domain.tasks import OutboundState, TaskState
    from friday.outbox import Kind

    task = await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
    await db.queue_outbound(
        task_id=task.id,
        conversation=task.conversation,
        kind=Kind.REPLY,
        sender="discord_user",
        text="cache đầy thôi, anh clear rồi nhé",
    )
    await _operator_said(db, "op-1", "à cái này do cache, anh clear rồi")

    await WorkflowRunner(db=db, auto_ask=True).run_once()

    (closed,) = await db.tasks()
    assert closed.state == TaskState.HANDLED_BY_OPERATOR
    (draft,) = await db.outbound()
    assert draft.state == OutboundState.CANCELLED
    assert await db.sendable_outbound() == []


async def test_a_message_this_process_posted_is_not_the_operator_answering(db):
    """Same account, different author. Our own ask must not close the task it
    is asking about."""
    from friday.outbox import Kind

    task = await make_task(db)
    row = await db.queue_outbound(
        task_id=task.id,
        conversation=task.conversation,
        kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user",
        text="cho anh xin cái correlationId nhé",
    )
    await db.mark_outbound_sent(row.id, sent_message_id="ours-1")
    await _operator_said(db, "ours-1", "cho anh xin cái correlationId nhé")

    await WorkflowRunner(db=db, auto_ask=False).run_once()

    assert (await db.tasks())[0].state != "handled_by_operator"


async def test_with_several_open_tasks_and_no_reply_nothing_closes(db):
    """Guessing which one they meant loses work. When it is not clear, the
    answer is a person, not a guess."""
    a = await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
    b = await make_task(db, curl="curl -X GET /pay")
    await _operator_said(db, "op-1", "để anh xem")

    await WorkflowRunner(db=db, auto_ask=False).run_once()

    states = {t.id: t.state for t in await db.tasks()}
    assert "handled_by_operator" not in states.values()


async def test_a_reply_picks_the_task_out_of_several(db):
    """Their reply names what it answers — the reporter's message, which is
    linked to a task."""
    from friday.domain.tasks import TaskState

    a = await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
    b = await make_task(db, curl="curl -X GET /pay")
    await db.record_message(make_event(message_id="report-b", text="curl lỗi"))
    await db.mark_triaged(make_event(message_id="report-b"), b.id, decision={"type": "api_issue"})
    await _operator_said(db, "op-1", "cái curl đó thiếu header", reply_to="report-b")

    await WorkflowRunner(db=db, auto_ask=False).run_once()

    states = {t.id: t.state for t in await db.tasks()}
    assert states[b.id] == TaskState.HANDLED_BY_OPERATOR
    assert states[a.id] != TaskState.HANDLED_BY_OPERATOR


async def test_a_handled_task_can_be_reopened_by_a_person(db):
    """Closing on "they said something in this channel" will sometimes be
    wrong, so it cannot be terminal."""
    from friday.domain.tasks import TaskState

    task = await make_task(db)
    await db.move_task(task.id, TaskState.HANDLED_BY_OPERATOR)
    await db.move_task(task.id, TaskState.PENDING)

    assert (await db.tasks())[0].state == TaskState.PENDING
