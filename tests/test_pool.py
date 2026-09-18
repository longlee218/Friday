"""Acting on tasks. The only reply allowed out without review is the request
for missing details: it is the same question every time, and being wrong about
it costs someone one unnecessary question."""

from __future__ import annotations

import pytest

from friday.domain.models import Task
from datetime import datetime, timezone

from conftest import ScriptedHarness, make_event
from friday.domain.conversation import ConversationId
from friday.tasks.pool import ASKED, Pool
from friday.domain.states import TaskState




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

    acted = await Pool(db=db, auto_ask=True).run_once()

    (queued,) = await db.outbound()
    assert queued.kind == "ask_for_details"
    assert queued.sender == "discord_user"
    assert queued.conversation.target_id == "watched"
    assert acted[0].state == ASKED


async def test_nothing_is_sent_when_auto_asking_is_off(db):
    await make_task(db)

    await Pool(db=db, auto_ask=False).run_once()

    assert [r for r in await db.outbound() if r.sender == "discord_user"] == []
    assert (await db.tasks())[0].state == "needs_human"


async def test_a_task_is_acted_on_only_once(db):
    await make_task(db)
    runner = Pool(db=db, auto_ask=True)

    await runner.run_once()
    await runner.run_once()

    assert len([r for r in await db.outbound() if r.sender == "discord_user"]) == 1


async def test_a_report_that_can_be_traced_waits_for_a_human(db):
    """Tracing is not built. Handing over is honest; replying would not be."""
    await make_task(db, correlation_id="7f3a91c2-dead-beef-cafe-1234567890ab")

    await Pool(db=db, auto_ask=True).run_once()

    assert [r for r in await db.outbound() if r.sender == "discord_user"] == []
    assert (await db.tasks())[0].state == "needs_human"


async def test_types_without_a_workflow_wait_for_a_human(db):
    await db.create_task(conversation=ConversationId("fake", "watched"), type="doc_question",
                         state="pending", confidence=0.9, params={"question": "?"})

    await Pool(db=db, auto_ask=True).run_once()

    assert [r for r in await db.outbound() if r.sender == "discord_user"] == []
    assert (await db.tasks())[0].state == "needs_human"


async def test_a_task_stops_being_asked_after_a_few_tries(db):
    """Asking forever is how a helpful question becomes noise. After the bound
    it becomes a human's problem, which is what a human is for."""
    opened = await make_task(db)
    # Debounce off: this is about the bound on asking, not about bursts.
    runner = Pool(db=db, auto_ask=True, max_asks=2)

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
        self.strangers: list[bool] = []
        self.states: list = []

    async def draft(self, *, asking, params=None, state=None,
                    stranger=False, context=(), tone=()):
        self.strangers.append(stranger)
        self.states.append(state)
        from friday.responder import Draft

        self.asked.append(asking)
        self.given_params.append(params)
        return Draft(self._text) if self._text else None


async def test_the_responder_is_told_what_to_say(db):
    await make_task(db)
    responder = StubResponder("ok")

    await Pool(db=db, auto_ask=True, responder=responder).run_once()

    assert "correlationId" in responder.asked[0]


async def test_the_template_still_goes_out_when_the_responder_cannot(db):
    """Never a wrong reply in the operator's name; never silence either."""
    await make_task(db)

    await Pool(db=db, auto_ask=True, responder=StubResponder(None)).run_once()

    (queued,) = await db.outbound()
    assert queued.kind == "ask_for_details"
    assert await db.sendable_outbound() != []


async def test_without_a_responder_nothing_changes(db):
    await make_task(db)

    await Pool(db=db, auto_ask=True).run_once()

    assert [r.kind for r in await db.outbound()] == ["ask_for_details"]


async def test_a_task_waiting_on_the_reporter_still_is(db):
    await make_task(db)

    acted = await Pool(db=db, auto_ask=True).run_once()

    assert acted[0].state == ASKED


# ---- who decides what ------------------------------------------------------


async def test_asking_for_details_is_the_agents_own_decision(db):
    """Asking is low-risk whoever phrased it. The operator is interrupted for
    answers and for trouble, not for questions."""
    await make_task(db)

    acted = await Pool(
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
    runner = Pool(db=db, auto_ask=True)

    await runner.run_once()

    assert (await db.tasks())[0].state == "needs_human"
    (card,) = await db.outbound()
    assert card.kind == "help_wanted"
    assert card.sender == "discord_bot"
    assert "abcdef01-2345-6789-abcd-ef0123456789" in card.text


async def test_the_operator_is_told_once(db):
    """A card per poll is a notification that trains you to ignore it."""
    await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
    runner = Pool(db=db, auto_ask=True)

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
    from friday.dag.engine import DAG, Node
    from friday.dag.router import EDGE_ROUTER, register_dag
    from friday.domain.actions import Reply

    async def answers(state, deps):
        return Reply("cache đầy thôi, anh clear rồi nhé")

    # A graph standing in for the real one. This test is about the approval
    # machinery, not about what `api_issue` investigates — it needs a route
    # that produces a `Reply` and nothing more.
    EDGE_ROUTER.pop("api_issue", None)
    register_dag("api_issue", DAG(name="answers", nodes=(Node("answer", answers),)))

    await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
    runner = Pool(db=db, auto_ask=True)

    try:
        acted = await runner.run_once()
    finally:
        EDGE_ROUTER.pop("api_issue", None)

    reply, card = await db.outbound()
    assert reply.kind == "reply"
    assert reply.text == "cache đầy thôi, anh clear rồi nhé"
    # Only the question is sendable; the answer waits to be answered.
    assert [r.kind for r in await db.sendable_outbound()] == ["approval_card"]
    # The card names the row it asks about, so answering it approves that
    # reply and not whatever the task queues next.
    assert card.approves == reply.id
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

    await Pool(db=db, auto_ask=True).run_once()

    # One `tasks_in_state` for the pending sweep, one for the announcement.
    assert queries == ["tasks_in_state", "tasks_in_state", "dag_pauses", "announced"]
    assert len(await db.outbound()) == 5


async def test_extraction_runs_when_a_message_is_linked(db):
    """End-to-end: a message is recorded, a task is linked to it, the runner
    reads the text, the registered extractor fills fields, the planner gets
    a complete Params and hands over. Without the link to original_text, the
    runner has nothing to extract from and the test would only prove the
    read path is plumbed."""
    from dataclasses import dataclass
    from datetime import datetime, timezone

    from sqlalchemy import update as sa_update

    from friday.store import schema
    from friday.config import AgentConfig
    from friday.domain.conversation import ConversationId
    from friday.extraction import _EXTRACTORS, build_extractor
    from friday.extraction.answer import answer_shape
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

    class StubHarness(ScriptedHarness):
        #: Subclassed rather than written from scratch, so `run_structured`
        #: — the validation and its correction turn — is the real one. A
        #: stub that supplied its own would let this pass while skipping the
        #: mechanism the extraction actually goes through.
        tool_turns = 0

        async def run(self, prompt, **kwargs):
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
        harness=StubHarness(answers=answer_shape(ApiIssueParams)),  # type: ignore[arg-type]
        name="api_issue_ext",
    )
    _EXTRACTORS["api_issue"] = ext
    # Extraction happens inside the graph's own entry node now (ticket 03),
    # the same `prepare` this stub extractor is wired into either way — no
    # need to remove `api_issue`'s registered graph to reach this path any
    # more, because there is only the one path.

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

        await Pool(db=db, auto_ask=False).run_once()

        # Extraction ran: the harness saw the reporter's text. The exact
        # HandOver action depends on validate's verdict — what matters here
        # is that the prompt was sent and the message's original text
        # reached the workflow.
        assert prompts_seen, "extractor was never called"
        assert "abcdef01-2345-6789" in prompts_seen[0]
        # And the workflow produced an outbound row of some kind, which is
        # how a task surfaces to a person.
        assert await db.outbound(), "no outbound produced"
    finally:
        _EXTRACTORS.pop("api_issue", None)


async def test_ask_clarification_reaches_the_reporter_in_the_responders_words(db):
    """Ticket 05, end to end: the report already has a curl, so the
    structural floor (`_traceable`) finds nothing wrong — the only reason an
    `Ask` exists at all is the extractor naming a field in `ask_about` that
    code does not require. The sentence that reaches the reporter is the
    Responder's, not the template `_question_from_clarify` builds for it to
    write from.
    """
    from agents.testing import ScriptedModel, assistant_message, function_call
    from sqlalchemy import update as sa_update

    from friday.agent.harness import Harness
    from friday.config import AgentConfig
    from friday.domain.models import ApiIssueParams, InboundEvent, MentionType
    from friday.extraction import _EXTRACTORS, build_extractor
    from friday.extraction.answer import answer_shape
    from friday.store import schema

    ext = build_extractor(
        params_cls=ApiIssueParams,
        harness=Harness(
            config=AgentConfig(
                name="api_issue_ext", api_key="k",
                base_url="https://example.invalid/v1", model="test-model",
            ),
            instructions="extract",
            answers=answer_shape(ApiIssueParams),
            model=ScriptedModel([[function_call("answer", {
                "ask_about": ["environment"],
                "because": "the curl doesn't say which server",
            }, call_id="1")]]),
        ),
        name="api_issue_ext",
    )
    _EXTRACTORS["api_issue"] = ext

    # Ticket 12: a draft naming no work, so this stays a test of whose
    # words reach the reporter rather than of `friday.responder.check`.
    responder = StubResponder("em đang chạy trên môi trường nào thế?")

    try:
        event = InboundEvent(
            provider="fake", provider_message_id="m-clarify-1", channel_id="watched",
            thread_id=None, author_id="u-reporter", author_name="reporter",
            text="checkout API bị lỗi rồi, curl -X GET /pay", created_at=datetime.now(timezone.utc),
            mention_type=MentionType.DIRECT,
        )
        await db.record_message(event)
        task = await make_task(db, curl="curl -X GET /pay")  # traceable already
        async with db._sessions.begin() as session:
            await session.execute(
                sa_update(schema.Message)
                .where(schema.Message.provider_message_id == "m-clarify-1")
                .values(task_id=task.id)
            )

        await Pool(db=db, auto_ask=True, responder=responder).run_once()

        (row,) = await db.outbound()
        assert row.text == "em đang chạy trên môi trường nào thế?", (
            "the reporter must see the Responder's sentence, not the template"
        )
        (asking,) = responder.asked
        assert "environment" in asking.lower(), (
            "the Responder must be told which field to ask about"
        )
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

    await Pool(db=db, auto_ask=True, responder=responder).run_once()

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


# --- ticket 08: the build respects a budget ---------------------------------


async def test_an_unset_budget_changes_nothing(db):
    """D7: the default, and the behaviour every install had before this
    ticket — bounded by the message count alone."""
    task = await make_task(db)
    await _said(db, "m1", "@Lee API lỗi rồi a ơi", secs=0, mention=True)
    await db.mark_triaged(
        make_event(message_id="m1"), task.id, decision={"type": "api_issue"}
    )
    await _said(db, "m2", "curl -X POST /pay trả 500, trên production", secs=3)

    with_budget = await db.original_text_for(task.id, budget_tokens=None)
    without_budget = await db.original_text_for(task.id)

    assert with_budget == without_budget
    assert "curl -X POST /pay" in with_budget


async def test_the_message_count_cap_still_binds_with_a_generous_budget(db):
    """D6: the count is a *secondary* cap, not a removed one — a pathological
    room with far more than `limit` messages must not grow the build past
    it, however large the budget is or whether one is configured at all."""
    task = await make_task(db)
    await _said(db, "m1", "@Lee API lỗi rồi a ơi", secs=0, mention=True)
    await db.mark_triaged(
        make_event(message_id="m1"), task.id, decision={"type": "api_issue"}
    )
    for n in range(2, 30):
        await _said(db, f"m{n}", f"chi tiết số {n}", secs=n)

    said = await db.original_text_for(task.id, limit=20, budget_tokens=1_000_000)

    assert said.count("chi tiết số") == 19, (
        "a huge budget must not undo the message-count cap"
    )


async def test_over_budget_the_oldest_messages_are_dropped_first(db):
    """D6: the budget is primary, the count secondary — and dropping starts
    from the oldest end, because an answer to a question just asked is the
    newest message and the one a follow-up pass cannot afford to lose."""
    task = await make_task(db)
    await _said(db, "m1", "@Lee " + ("API lỗi rồi. " * 40), secs=0, mention=True)
    await db.mark_triaged(
        make_event(message_id="m1"), task.id, decision={"type": "api_issue"}
    )
    await _said(db, "m2", "correlationId là abcdef01-2345-6789-abcd-ef0123456789", secs=3)

    said = await db.original_text_for(task.id, budget_tokens=20)

    assert "API lỗi rồi" not in said, "the oldest message should have been dropped"
    assert "abcdef01-2345-6789-abcd-ef0123456789" in said, (
        "the newest message must survive the drop"
    )


async def test_a_budget_the_transcript_already_fits_changes_nothing(db):
    task = await make_task(db)
    await _said(db, "m1", "@Lee API lỗi rồi", secs=0, mention=True)
    await db.mark_triaged(
        make_event(message_id="m1"), task.id, decision={"type": "api_issue"}
    )

    said = await db.original_text_for(task.id, budget_tokens=10_000)

    assert "API lỗi rồi" in said


async def test_the_newest_message_survives_even_alone_over_budget(db):
    """A single message larger than the whole budget is not something
    dropping older messages can fix — it is returned whole regardless, never
    emptied. `record_ineffective_compaction` is what makes this visible,
    not a truncated or missing answer."""
    task = await make_task(db)
    huge = "@Lee " + ("API lỗi rồi rất là dài. " * 200)
    await _said(db, "m1", huge, secs=0, mention=True)
    await db.mark_triaged(
        make_event(message_id="m1"), task.id, decision={"type": "api_issue"}
    )

    said = await db.original_text_for(task.id, budget_tokens=5)

    assert "API lỗi rồi" in said


async def test_ineffective_compactions_are_counted_and_trip_the_cooldown(db):
    task = await make_task(db)

    first = await db.record_ineffective_compaction(task.id)
    assert first == 1
    assert await db.compaction_on_cooldown(task.id) is False

    second = await db.record_ineffective_compaction(task.id)
    assert second == 2
    assert await db.compaction_on_cooldown(task.id) is True


async def test_a_task_with_no_ineffective_compaction_is_not_on_cooldown(db):
    task = await make_task(db)

    assert await db.compaction_on_cooldown(task.id) is False


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
    from friday.domain.states import OutboundState, TaskState
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

    await Pool(db=db, auto_ask=True).run_once()

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

    await Pool(db=db, auto_ask=False).run_once()

    assert (await db.tasks())[0].state != "handled_by_operator"


async def test_with_several_open_tasks_and_no_reply_nothing_closes(db):
    """Guessing which one they meant loses work. When it is not clear, the
    answer is a person, not a guess."""
    a = await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
    b = await make_task(db, curl="curl -X GET /pay")
    await _operator_said(db, "op-1", "để anh xem")

    await Pool(db=db, auto_ask=False).run_once()

    states = {t.id: t.state for t in await db.tasks()}
    assert "handled_by_operator" not in states.values()


async def test_a_reply_picks_the_task_out_of_several(db):
    """Their reply names what it answers — the reporter's message, which is
    linked to a task."""
    from friday.domain.states import TaskState

    a = await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
    b = await make_task(db, curl="curl -X GET /pay")
    await db.record_message(make_event(message_id="report-b", text="curl lỗi"))
    await db.mark_triaged(make_event(message_id="report-b"), b.id, decision={"type": "api_issue"})
    await _operator_said(db, "op-1", "cái curl đó thiếu header", reply_to="report-b")

    await Pool(db=db, auto_ask=False).run_once()

    states = {t.id: t.state for t in await db.tasks()}
    assert states[b.id] == TaskState.HANDLED_BY_OPERATOR
    assert states[a.id] != TaskState.HANDLED_BY_OPERATOR


async def test_a_handled_task_can_be_reopened_by_a_person(db):
    """Closing on "they said something in this channel" will sometimes be
    wrong, so it cannot be terminal."""
    from friday.domain.states import TaskState

    task = await make_task(db)
    await db.move_task(task.id, TaskState.HANDLED_BY_OPERATOR)
    await db.move_task(task.id, TaskState.PENDING)

    assert (await db.tasks())[0].state == TaskState.PENDING


# --- ticket 41: a stranger changes the pronouns, and nothing else ------------


async def test_someone_the_operator_never_wrote_to_is_a_stranger(db):
    responder = StubResponder("dạ anh/chị gửi mình correlationId nhé")
    task = await make_task(db)
    await db.record_message(make_event(message_id="m1", author_id="newcomer"))
    await db.mark_triaged(make_event(message_id="m1", author_id="newcomer"), task.id, decision={"type": "api_issue"})

    await Pool(db=db, auto_ask=True, responder=responder).run_once()

    assert responder.strangers == [True]


async def test_an_exchange_in_either_direction_makes_them_known(db):
    """A reply is the smallest thing that is an actual exchange. Being in the
    same channel is not — the operator has spoken in every watched channel."""
    responder = StubResponder("cho anh xin correlationId nhé")
    task = await make_task(db)
    await db.record_message(make_event(message_id="m1", author_id="dana"))
    await db.mark_triaged(make_event(message_id="m1", author_id="dana"), task.id, decision={"type": "api_issue"})
    # Long once replied to something dana said.
    await db.record_message(make_event(message_id="old", author_id="dana", text="hi"), context_only=True)
    await db.record_message(
        make_event(message_id="long-said", author_id="me", is_own=True, text="hi em", reply_to="old"),
        context_only=True,
    )

    await Pool(db=db, auto_ask=True, responder=responder).run_once()

    assert responder.strangers == [False]


async def test_being_written_down_for_the_room_makes_them_known(db, tmp_path):
    from friday.memory.channel_context import ContextStore
    from friday.responder import Responder

    store = ContextStore(tmp_path)
    store.init_channel("watched", overrides={"people": {"reporter": "thân"}})
    store.hold_all()

    class Stub(StubResponder):
        def __init__(self):
            super().__init__("ok")
            self._context = store
        knows = Responder.knows

    responder = Stub()
    task = await make_task(db)
    await db.record_message(make_event(message_id="m1", author_name="reporter"))
    await db.mark_triaged(make_event(message_id="m1", author_name="reporter"), task.id, decision={"type": "api_issue"})

    await Pool(db=db, auto_ask=True, responder=responder).run_once()

    assert responder.strangers == [False]


async def test_the_stranger_line_reaches_the_prompt_and_only_then(tmp_path):
    from agents.models.interface import Model

    from friday.config import AgentConfig
    from friday.responder import Responder

    prompts: list[str] = []

    class Capture(Model):
        async def get_response(self, system_instructions, input, *a, **kw):
            prompts.append(str(input))
            raise RuntimeError("captured")

        def stream_response(self, *a, **kw):
            raise NotImplementedError

    responder = Responder(
        config=AgentConfig(name="r", api_key="k", base_url="http://x/v1", model="m"),
        model=Capture(),
    )
    await responder.draft(asking="q", stranger=True)
    await responder.draft(asking="q", stranger=False)

    assert "not written to this person" in prompts[0]
    assert "counterpart" not in prompts[1]


# --- ticket 35: tell the operator what they were asked -----------------------


async def _reporter_said(db, message_id, text, *, secs, task_id=None, reply_to=None):
    from datetime import timedelta

    event = make_event(
        message_id=message_id, text=text, mention_type=None, reply_to=reply_to,
        created_at=datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc) + timedelta(seconds=secs),
    )
    await db.record_message(event, context_only=task_id is None)
    if task_id is not None:
        await db.mark_triaged(event, task_id, decision={"type": "api_issue"})


async def test_the_operator_is_told_what_the_reporter_asked(db):
    """The announcement was the type and the parameters — all true, none of it
    the reason the task stopped. The reporter asked what a correlationId is;
    the operator saw a task with one in it and no hint that a person was
    waiting on a sentence they could type in five seconds."""
    task = await make_task(db)
    await _reporter_said(db, "m1", "API lỗi rồi", secs=0, task_id=task.id)
    await _reporter_said(db, "m2", "correlationId là cái gì a nhỉ?", secs=30, reply_to="m1")
    await db.move_task(task.id, TaskState.NEEDS_HUMAN)

    await Pool(db=db, auto_ask=False).run_once()

    (told,) = [r for r in await db.outbound() if r.kind == "help_wanted"]
    assert "correlationId là cái gì a nhỉ?" in told.text


async def test_nothing_is_added_when_they_said_nothing_since(db):
    task = await make_task(db)
    await _reporter_said(db, "m1", "API lỗi rồi", secs=0, task_id=task.id)
    await db.move_task(task.id, TaskState.NEEDS_HUMAN)

    await Pool(db=db, auto_ask=False).run_once()

    (told,) = [r for r in await db.outbound() if r.kind == "help_wanted"]
    assert "they last said" not in told.text


async def test_our_own_question_is_not_what_they_last_said(db):
    from friday.outbox import Kind

    task = await make_task(db)
    await _reporter_said(db, "m1", "API lỗi rồi", secs=0, task_id=task.id)
    row = await db.queue_outbound(
        task_id=task.id, conversation=task.conversation, kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user", text="cho anh xin correlationId",
    )
    await db.mark_outbound_sent(row.id, sent_message_id="ours")
    await _reporter_said(db, "ours", "cho anh xin correlationId", secs=10, reply_to="m1")
    await db.move_task(task.id, TaskState.NEEDS_HUMAN)

    await Pool(db=db, auto_ask=False).run_once()

    (told,) = [r for r in await db.outbound() if r.kind == "help_wanted"]
    assert "they last said" not in told.text


async def test_talking_about_something_else_is_not_about_this_task(db):
    """One person filing four reports in one channel produced a new
    announcement for every task each time they typed. What they said about
    something else is not about this; a reply names what it is about."""
    task = await make_task(db)
    await _reporter_said(db, "m1", "API lỗi rồi", secs=0, task_id=task.id)
    await _reporter_said(db, "m2", "trưa nay ăn gì mọi người", secs=30)
    await db.move_task(task.id, TaskState.NEEDS_HUMAN)

    await Pool(db=db, auto_ask=False).run_once()

    (told,) = [r for r in await db.outbound() if r.kind == "help_wanted"]
    assert "trưa nay" not in told.text


# --- ticket 12: a pending patch does not outlive its task -------------------


async def _paused_on_a_patch(db):
    """A task sitting exactly where ticket 07 leaves one: handed over, with an
    approval waiting on the row."""
    task = await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
    await db.move_task(task.id, TaskState.NEEDS_HUMAN)
    await db.save_dag_state(
        task.id,
        dag_name="api_issue",
        results={},
        params_fingerprint="whatever",
        paused_at_node="fix_bug",
        paused_question="Found a fix. It needs approval before I use it.",
        interruption={"current_agent": "dag_fix"},
    )
    return task






async def test_the_calls_a_task_causes_are_stamped_with_that_task(db):
    """The reason `task_id` exists, driven where it actually happens.

    An extractor runs inside a task's graph, on every pass, against every
    message the reporter has sent — so the message a call was "about" is not a
    question with one answer, and until now those rows landed under no key at
    all. This is the only test that would notice the chain from `Pool` through
    `prepare_node` to the extractor's `Harness` coming apart in the middle,
    which is exactly how the `record=` chain broke one ticket ago.
    """
    from datetime import datetime, timezone

    from agents.testing import ScriptedModel, assistant_message
    from sqlalchemy import update as sa_update

    from friday.agent.harness import Harness
    from friday.config import AgentConfig
    from friday.domain.models import ApiIssueParams, InboundEvent, MentionType
    from friday.extraction import _EXTRACTORS, build_extractor
    from friday.extraction.answer import answer_shape
    from friday.store import schema

    recorded: list = []

    async def sink(call) -> None:
        recorded.append(call)

    # Deliberately incomplete: a complete report ends the graph without ever
    # asking, and the ask is what reaches the responder — the *other* agent
    # that stamps a task id, and the one that writes text a person reads.
    filled = '{"summary": "checkout 500", "environment": "production", "curl": null}'
    ext = build_extractor(
        params_cls=ApiIssueParams,
        harness=Harness(
            config=AgentConfig(
                name="api_issue_extractor", api_key="k",
                base_url="https://example.invalid/v1", model="test-model",
            ),
            instructions="lift the fields out",
            answers=answer_shape(ApiIssueParams),
            model=ScriptedModel([[assistant_message(filled)]]),
            record=sink,
        ),
        name="api_issue_extractor",
    )
    kept = _EXTRACTORS.get("api_issue")
    _EXTRACTORS["api_issue"] = ext

    try:
        await db.record_message(
            InboundEvent(
                provider="fake", provider_message_id="m-1", channel_id="watched",
                thread_id=None, author_id="u", author_name="reporter",
                text="production broke at noon",
                created_at=datetime.now(timezone.utc),
                mention_type=MentionType.DIRECT,
            )
        )
        task = await make_task(db)
        async with db._sessions.begin() as session:
            await session.execute(
                sa_update(schema.Message)
                .where(schema.Message.provider_message_id == "m-1")
                .values(task_id=task.id)
            )

        await Pool(db=db, auto_ask=True, responder=_responder(sink)).run_once()
    finally:
        if kept is None:
            _EXTRACTORS.pop("api_issue", None)
        else:
            _EXTRACTORS["api_issue"] = kept

    by_agent = {c.agent: c for c in recorded}
    assert set(by_agent) == {"api_issue_extractor", "responder"}, (
        "both legs of the chain, because either can come apart on its own"
    )
    assert by_agent["api_issue_extractor"].task_id == task.id
    assert by_agent["api_issue_extractor"].node == "prepare"
    assert by_agent["api_issue_extractor"].message_id is None, (
        "an extractor reads a task, not one message"
    )
    assert by_agent["responder"].task_id == task.id
    assert by_agent["responder"].node is None, "a responder is not a graph node"


def _responder(sink):
    """A real `Responder` over a scripted model, so the whole path from
    `Pool` to `Harness` is exercised rather than stubbed at the first joint."""
    from agents.testing import ScriptedModel, assistant_message

    from friday.config import AgentConfig
    from friday.responder import Responder

    return Responder(
        config=AgentConfig(
            name="responder", api_key="k",
            base_url="https://example.invalid/v1", model="test-model",
        ),
        model=ScriptedModel([[assistant_message("cho anh xin correlationId nhé")]]),
        record=sink,
    )


async def test_the_path_a_task_walked_survives_the_pause_that_ended_it(db):
    """The trail is checkpointed, and then a hand-over used to blank it.

    `_walk`'s checkpoint saves the path; the `HandOver` branch immediately
    calls `_record_pause` on the same row, and that call passed `results` but
    not `trail` — so the upsert overwrote the path with the empty default it
    had just been given. `results` survived because somebody remembered to
    pass them.

    Driven through `Pool.run_once` over a two-node graph, because a one-node
    graph never reaches `_walk` at all: node 0 runs outside the runner and
    every graph registered today ends there, which is why nothing noticed.
    """
    from friday.dag.engine import DAG, Edge, Node
    from friday.dag.router import EDGE_ROUTER, register_dag
    from friday.domain.actions import HandOver

    async def looks(state, deps):
        return "nothing in the logs"

    async def gives_up(state, deps):
        return HandOver("no idea, over to you")

    EDGE_ROUTER.pop("api_issue", None)
    register_dag(
        "api_issue",
        DAG(
            name="two-steps",
            nodes=(Node("prepare", _ready), Node("look", looks), Node("give_up", gives_up)),
            edges=(Edge("prepare", "look"), Edge("look", "give_up")),
        ),
    )
    task = await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")

    try:
        await Pool(db=db, auto_ask=True).run_once()
        stored = await db.load_dag_progress(task.id, dag_name="two-steps")

        # And again, the way a restart or a second pass reaches it: every node
        # is already recorded, so the runner walks past all of them and adds
        # nothing. Whatever the path is now, it came out of the database.
        await db.move_task(task.id, TaskState.PENDING)
        await Pool(db=db, auto_ask=True).run_once()
        resumed = await db.load_dag_progress(task.id, dag_name="two-steps")
    finally:
        EDGE_ROUTER.pop("api_issue", None)

    assert stored is not None
    _, walked = stored
    assert walked == ["look", "give_up"], (
        "the path the run took, still there after the pause that ended it"
    )

    assert resumed is not None
    _, walked_again = resumed
    assert walked_again == ["look", "give_up"], (
        "a resumed pass reads the path back rather than rebuilding it from "
        "the part it happened to walk itself"
    )


async def test_the_checkpoint_is_what_saves_the_path_when_nothing_pauses(db):
    """A graph that answers never calls `_record_pause`, so the checkpoint is
    the only thing that can have written the path.

    Worth its own test rather than an assertion on the one above: with the
    pause carrying the trail too, deleting `trail=path` from the checkpoint
    left that test green. Two writers, one of which masked the other — which
    is the same shape as the bug it was written for.
    """
    from friday.dag.engine import DAG, Edge, Node
    from friday.dag.router import EDGE_ROUTER, register_dag
    from friday.domain.actions import Reply

    async def looks(state, deps):
        return "found it"

    async def answers(state, deps):
        return Reply("cache đầy thôi, anh clear rồi nhé")

    EDGE_ROUTER.pop("api_issue", None)
    register_dag(
        "api_issue",
        DAG(
            name="answering",
            nodes=(Node("prepare", _ready), Node("look", looks), Node("answer", answers)),
            edges=(Edge("prepare", "look"), Edge("look", "answer")),
        ),
    )
    task = await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")

    try:
        await Pool(db=db, auto_ask=True).run_once()
        stored = await db.load_dag_progress(task.id, dag_name="answering")
    finally:
        EDGE_ROUTER.pop("api_issue", None)

    assert stored is not None
    _, walked = stored
    assert walked == ["look", "answer"]


async def _ready(state, deps):
    """A node 0 that hands its parameters on without a model, so a graph test
    is about the graph."""
    from friday.domain.models import ApiIssueParams

    return ApiIssueParams(**deps.task.params)


async def test_a_draft_that_would_promise_something_never_reaches_the_reporter(db):
    """The only message this system sends without a person reading it first.

    `config.yaml` justified that with a sentence — what is being asked never
    changes, only the wording does — and nothing enforced it. The responder's
    input carries other people's channel messages, so this was both the path
    with no human in it and the path whose wording a model writes from
    untrusted text under the operator's name.

    Driven here rather than at the check, because what matters is not that a
    predicate returns something: it is which bytes are in the row that goes
    out.
    """
    await make_task(db)
    responder = StubResponder("ok có correlationId rồi, để anh trace thử")

    await Pool(db=db, auto_ask=True, responder=responder).run_once()

    (queued,) = await db.outbound()
    assert "để anh trace" not in queued.text
    assert queued.text == responder.asked[0], "the template, unchanged"
    assert "correlationId" in queued.text


async def test_a_draft_that_only_reworded_the_question_does_reach_them(db):
    """The wording is allowed to change — that is the whole reason the
    responder is asked. A floor that only accepted the template would make the
    step pointless."""
    await make_task(db)
    responder = StubResponder(
        "anh ơi cho em xin cái correlationId với, hoặc cái curl anh gọi nhé"
    )

    await Pool(db=db, auto_ask=True, responder=responder).run_once()

    (queued,) = await db.outbound()
    assert queued.text.startswith("anh ơi cho em xin")


# --- ticket 46: the operator answering first is the case that is missed ------


async def _reported(db, task_id, message_id, *, secs, text="@Lee API lỗi rồi a ơi"):
    """The mention that opened `task_id`, written `secs` from now.

    Linked the way triage links it, because the link is what this ticket's
    fix reads: a task with no message attached keeps comparing against its own
    row, and that case is deliberately unchanged.
    """
    from datetime import timedelta

    event = make_event(
        message_id=message_id,
        text=text,
        created_at=datetime.now(timezone.utc) + timedelta(seconds=secs),
    )
    await db.record_message(event)
    await db.mark_triaged(event, task_id, decision={"type": "api_issue"})


async def test_an_answer_written_before_the_task_row_existed_still_closes_it(db):
    """The operator answering *fast* is the case that gets missed.

    A mention opens a turn, the turn closes after `turn_seconds`, and triage
    polls every two seconds — so the task row is created some fourteen seconds
    after the reporter wrote. Comparing the operator's message against
    `task.created_at` therefore sorts an answer typed inside that window
    *before* the task it answers, and it never counts.

    Worse, the operator speaking is itself what closes the reporter's turn, so
    replying quickly is what pushes `task.created_at` past their own message.
    The faster they are, the more certain the miss.
    """
    task = await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
    await _reported(db, task.id, "report-1", secs=-14)
    await _operator_said(db, "op-1", "à cái này anh xử lý rồi", secs=-9)

    await Pool(db=db, auto_ask=True).run_once()

    (closed,) = await db.tasks()
    assert closed.state == TaskState.HANDLED_BY_OPERATOR


async def test_a_backfilled_task_sees_the_answer_in_its_own_history(db):
    """The sweep recovering a mention after downtime is the same shape at a
    larger scale: the task row is created now, every message in it was written
    hours ago. Against `task.created_at` *no* answer in recovered history can
    ever count, so the agent re-asks a reporter what the operator already
    answered by hand while the process was down.
    """
    task = await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
    await _reported(db, task.id, "report-1", secs=-20 * 3600)
    await _operator_said(db, "op-1", "anh trả lời trên thread rồi nhé", secs=-19 * 3600)

    await Pool(db=db, auto_ask=True).run_once()

    (closed,) = await db.tasks()
    assert closed.state == TaskState.HANDLED_BY_OPERATOR


async def test_what_the_operator_said_before_the_report_is_not_an_answer_to_it(db):
    """The line moves; it is not removed. Whatever they were talking about
    before the reporter wrote cannot be an answer to a report that did not
    exist yet — and reading it as one would close tasks on unrelated chatter,
    which is a worse failure than the one this ticket fixes.
    """
    task = await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
    await _operator_said(db, "op-0", "sáng nay deploy xong rồi nha", secs=-30)
    await _reported(db, task.id, "report-1", secs=-14)

    await Pool(db=db, auto_ask=True).run_once()

    (task_now,) = await db.tasks()
    assert task_now.state != TaskState.HANDLED_BY_OPERATOR


async def test_a_task_with_no_message_attached_still_uses_its_own_row(db):
    """A manually seeded task has nothing to compare against but itself.
    `source_message_of` names this case — a seeded task, or a follow-up whose
    linkage was lost — and it must keep behaving exactly as it did.
    """
    task = await make_task(db)
    await _operator_said(db, "op-1", "anh xử lý rồi", secs=10)

    await Pool(db=db, auto_ask=False).run_once()

    (closed,) = await db.tasks()
    assert closed.id == task.id
    assert closed.state == TaskState.HANDLED_BY_OPERATOR


# Ticket 13 (board read-it-the-way-the-operator-does): a long graph must not
# hold the pool. Every graph used to be awaited in turn, so a five-minute
# `api_issue` meant five minutes in which nothing else was asked, drafted or
# handed over.


def _graph(task_type, node):
    from friday.dag.engine import DAG, Node
    from friday.dag.router import EDGE_ROUTER, register_dag

    EDGE_ROUTER.pop(task_type, None)
    register_dag(task_type, DAG(name=f"{task_type}-test", nodes=(Node("only", node),)))


async def _doc_task(db):
    return await db.create_task(conversation=ConversationId("fake", "watched"),
                                type="doc_question", state="pending",
                                confidence=0.9, params={"question": "?"})


async def test_a_quick_graph_is_not_held_behind_a_slow_one(db):
    """The slow graph is first in the batch and cannot finish until the quick
    one has — so a pool that awaits them in turn never finishes at all."""
    import asyncio

    from friday.domain.actions import HandOver

    finished: list[str] = []
    quick_done = asyncio.Event()

    async def slow(state, deps):
        await quick_done.wait()
        finished.append("slow")
        return HandOver("slow")

    async def quick(state, deps):
        finished.append("quick")
        quick_done.set()
        return HandOver("quick")

    _graph("api_issue", slow)
    _graph("doc_question", quick)
    await make_task(db)
    await _doc_task(db)

    acted = await asyncio.wait_for(Pool(db=db, auto_ask=True).run_once(), 5)

    assert finished == ["quick", "slow"]
    assert len(acted) == 2


async def test_no_more_graphs_run_at_once_than_configured(db):
    import asyncio

    from friday.domain.actions import HandOver

    running = 0
    most = 0

    async def counted(state, deps):
        nonlocal running, most
        running += 1
        most = max(most, running)
        await asyncio.sleep(0.02)
        running -= 1
        return HandOver("done")

    _graph("api_issue", counted)
    for _ in range(4):
        await make_task(db)

    acted = await Pool(db=db, auto_ask=True, concurrency=2).run_once()

    assert len(acted) == 4
    assert most == 2


async def test_one_task_is_never_acted_on_twice_at_once(db):
    """A task stays `pending` for as long as its graph runs, so a second pass
    that starts meanwhile reads it as work waiting. It must leave it alone."""
    import asyncio

    from friday.domain.actions import HandOver

    entered = 0
    inside = asyncio.Event()
    release = asyncio.Event()

    async def held(state, deps):
        nonlocal entered
        entered += 1
        inside.set()
        await release.wait()
        return HandOver("done")

    _graph("api_issue", held)
    await make_task(db)
    pool = Pool(db=db, auto_ask=True)

    first = asyncio.create_task(pool.run_once())
    await asyncio.wait_for(inside.wait(), 5)
    second = await asyncio.wait_for(pool.run_once(), 5)
    release.set()
    (done,) = await asyncio.wait_for(first, 5)

    assert second == []
    assert entered == 1
    assert done.state == "needs_human"


async def test_the_bound_is_the_one_configured(db):
    """`Pool.build` is how the composition root makes the pool; a knob it does
    not pass on is a line in `config.yaml` that changes nothing."""
    import asyncio
    from types import SimpleNamespace

    from friday.domain.actions import HandOver

    running = 0
    most = 0

    async def counted(state, deps):
        nonlocal running, most
        running += 1
        most = max(most, running)
        await asyncio.sleep(0.02)
        running -= 1
        return HandOver("done")

    _graph("api_issue", counted)
    for _ in range(4):
        await make_task(db)
    config = SimpleNamespace(workflows=SimpleNamespace(
        auto_ask_for_details=True, max_asks=3, concurrency=3))

    await Pool.build(config, db=db).run_once()

    assert most == 3
