"""Acting on tasks. The only reply allowed out without review is the request
for missing details: it is the same question every time, and being wrong about
it costs someone one unnecessary question."""

from __future__ import annotations

from datetime import UTC, datetime

from conftest import ScriptedHarness, make_event

from friday.kernel.domain.conversation import ConversationId
from friday.kernel.domain.states import TaskState
from friday.kernel.pool.pool import ASKED, Pool


async def make_task(db, **params):
    return await db.create_task(
        conversation=ConversationId("fake", "watched"),
        type="backend.trace_problem",
        state="pending",
        confidence=0.9,
        params={
            "summary": "checkout 500",
            "environment": None,
            "correlation_id": None,
            "curl": None,
            **params,
        },
    )


async def make_request_permission(db, **params):
    """A task of the one other in-core type still built as `prepare`, then
    ask or hand over — nothing past node 0, so this decides without a
    workflow (`friday.kernel.dag.router.build_simple_dag`).

    Stands in for `backend.trace_problem` below wherever a test is really about
    the *generic* ask/escalate/responder mechanics, not about anything
    `trace_problem` investigates: `Intake` (ticket 6) replaced `prepare` for that
    type, makes no model call, and never asks or hands over at node 0 — so a
    task with nothing in it no longer produces the "missing details" `Ask`
    these tests are for.
    """
    return await db.create_task(
        conversation=ConversationId("fake", "watched"),
        type="ops.request_permission",
        state="pending",
        confidence=0.9,
        params={"project": "", "permission": "", "summary": "", **params},
    )


async def test_a_report_missing_details_is_asked_about(db):
    await make_request_permission(db)

    acted = await Pool(db=db, auto_ask=True).run_once()

    (queued,) = await db.outbound()
    assert queued.kind == "ask_for_details"
    assert queued.sender == "discord_user"
    assert queued.conversation.target_id == "watched"
    assert acted[0].state == ASKED


async def test_nothing_is_sent_when_auto_asking_is_off(db):
    await make_request_permission(db)

    await Pool(db=db, auto_ask=False).run_once()

    assert [r for r in await db.outbound() if r.sender == "discord_user"] == []
    assert (await db.tasks())[0].state == "needs_human"


async def test_a_task_is_acted_on_only_once(db):
    await make_request_permission(db)
    runner = Pool(db=db, auto_ask=True)

    await runner.run_once()
    await runner.run_once()

    assert len([r for r in await db.outbound() if r.sender == "discord_user"]) == 1


async def test_a_report_that_can_be_traced_waits_for_a_human(db, workflows):
    """Tracing is not built. Handing over is honest; replying would not be.

    `Intake` (ticket 6) never hands over at node 0 — it makes no model call
    and no longer treats findability as a gate — so the run reaches
    `Acknowledge` regardless. The unapproved "đang xử lý" it sends is not an
    answer; the task still ends up with a human once `Diagnose` has nothing
    to add.
    """
    await make_task(db, curl="curl https://api.aperogroup.ai/v1/pay")

    await Pool(db=db, auto_ask=True).run_once()

    assert [r.kind for r in await db.outbound() if r.sender == "discord_user"] == [
        "acknowledged",
    ]
    assert (await db.tasks())[0].state == "needs_human"


async def test_types_without_a_workflow_wait_for_a_human(db):
    await db.create_task(
        conversation=ConversationId("fake", "watched"),
        type="backend.answer_question",
        state="pending",
        confidence=0.9,
        params={"question": "?"},
    )

    await Pool(db=db, auto_ask=True).run_once()

    assert [r for r in await db.outbound() if r.sender == "discord_user"] == []
    assert (await db.tasks())[0].state == "needs_human"


async def test_a_task_stops_being_asked_after_a_few_tries(db):
    """Asking forever is how a helpful question becomes noise. After the bound
    it becomes a human's problem, which is what a human is for."""
    opened = await make_request_permission(db)
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

    async def draft(
        self, *, asking, params=None, state=None, stranger=False, context=(), tone=()
    ):
        self.strangers.append(stranger)
        self.states.append(state)
        from friday.kernel.responder import Draft

        self.asked.append(asking)
        self.given_params.append(params)
        return Draft(self._text) if self._text else None


async def test_the_responder_is_told_what_to_say(db):
    await make_request_permission(db)
    responder = StubResponder("ok")

    await Pool(db=db, auto_ask=True, responder=responder).run_once()

    assert "project" in responder.asked[0]


async def test_the_template_still_goes_out_when_the_responder_cannot(db):
    """Never a wrong reply in the operator's name; never silence either."""
    await make_request_permission(db)

    await Pool(db=db, auto_ask=True, responder=StubResponder(None)).run_once()

    (queued,) = await db.outbound()
    assert queued.kind == "ask_for_details"
    assert await db.sendable_outbound() != []


async def test_without_a_responder_nothing_changes(db):
    await make_request_permission(db)

    await Pool(db=db, auto_ask=True).run_once()

    assert [r.kind for r in await db.outbound()] == ["ask_for_details"]


async def test_a_task_waiting_on_the_reporter_still_is(db):
    await make_request_permission(db)

    acted = await Pool(db=db, auto_ask=True).run_once()

    assert acted[0].state == ASKED


# ---- who decides what ------------------------------------------------------


async def test_asking_for_details_is_the_agents_own_decision(db):
    """Asking is low-risk whoever phrased it. The operator is interrupted for
    answers and for trouble, not for questions."""
    await make_request_permission(db)

    acted = await Pool(
        db=db, auto_ask=True, responder=StubResponder("cho anh xin cái curl")
    ).run_once()

    (queued,) = await db.outbound()
    assert queued.kind == "ask_for_details"
    assert queued.text == "cho anh xin cái curl"
    assert await db.sendable_outbound() == [queued]
    assert acted[0].state == ASKED


async def test_a_task_it_cannot_handle_is_brought_to_the_operator(db, workflows):
    """Otherwise it sits in a column nobody is watching.

    `Intake` (ticket 6) acknowledges and reports before giving up now, so
    the operator's card is one of several rows rather than the only one —
    the acknowledgement and the report's own finding card precede it.
    """
    await make_task(
        db, curl="curl https://api.aperogroup.ai/v1/pay"
    )  # findable, unactionable
    runner = Pool(db=db, auto_ask=True)

    await runner.run_once()

    assert (await db.tasks())[0].state == "needs_human"
    (card,) = [r for r in await db.outbound() if r.kind == "help_wanted"]
    assert card.sender == "discord_bot"
    assert "curl https://api.aperogroup.ai/v1/pay" in card.text


async def test_the_operator_is_told_once(db, workflows):
    """A card per poll is a notification that trains you to ignore it.

    The investigation's own rows — the acknowledgement, the report's finding
    card — are queued once each on the pass that reaches them; only
    `help_wanted`, `_raise_hands`'s own row, is what repeated polling risks
    duplicating, and it does not.
    """
    await make_task(db, curl="curl https://api.aperogroup.ai/v1/pay")
    runner = Pool(db=db, auto_ask=True)

    await runner.run_once()
    await runner.run_once()
    await runner.run_once()

    assert [r.kind for r in await db.outbound()] == [
        "acknowledged",
        "finding",
        "help_wanted",
    ]


# The two debounce tests that lived here are gone with the debounce. A burst is
# absorbed before it reaches this loop now — see the turn tests in
# `test_triage_runner.py` — so there is nothing here to settle.


async def test_an_answer_from_a_workflow_waits_for_approval(db):
    """This is the producer the approval path never had. A workflow that can
    actually answer something says so, and the operator decides whether it goes
    out under their name."""
    from friday.kernel.dag.router import EDGE_ROUTER, register_dag
    from friday.sdk.actions import Reply
    from friday.sdk.workflow import DAG, Node

    async def answers(state, deps):
        return Reply("cache đầy thôi, anh clear rồi nhé")

    # A graph standing in for the real one. This test is about the approval
    # machinery, not about what `trace_problem` investigates — it needs a route
    # that produces a `Reply` and nothing more.
    EDGE_ROUTER.pop("backend.trace_problem", None)
    register_dag(
        "backend.trace_problem", DAG(name="answers", nodes=(Node("answer", answers),))
    )

    await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
    runner = Pool(db=db, auto_ask=True)

    try:
        acted = await runner.run_once()
    finally:
        EDGE_ROUTER.pop("backend.trace_problem", None)

    reply, card = await db.outbound()
    assert reply.kind == "reply"
    assert reply.text == "cache đầy thôi, anh clear rồi nhé"
    # Only the question is sendable; the answer waits to be answered.
    assert [r.kind for r in await db.sendable_outbound()] == ["approval_card"]
    # The card names the row it asks about, so answering it approves that
    # reply and not whatever the task queues next.
    assert card.approves == reply.id
    assert acted[0].state == "review"


async def test_a_drafted_reply_is_redacted_and_the_card_tells_the_truth(db):
    """Redaction runs on the draft (§12): a secret a workflow puts in an answer
    is scrubbed before the reply is queued, so it never leaves even if approved.
    The card shows those exact scrubbed bytes and flags that a redaction
    happened — the operator approves what will go out, told the truth about it."""
    from friday.kernel.dag.router import EDGE_ROUTER, register_dag
    from friday.sdk.actions import Reply
    from friday.sdk.workflow import DAG, Node

    async def leaks(state, deps):
        return Reply("cleared it — key was sk-abcdefghijklmnopqrstuvwxyz01")

    EDGE_ROUTER.pop("backend.trace_problem", None)
    register_dag(
        "backend.trace_problem", DAG(name="leaks", nodes=(Node("answer", leaks),))
    )

    await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
    try:
        await Pool(db=db, auto_ask=True).run_once()
    finally:
        EDGE_ROUTER.pop("backend.trace_problem", None)

    reply, card = await db.outbound()
    # What will actually be sent no longer carries the secret.
    assert "sk-abcdefghijklmnopqrstuvwxyz01" not in reply.text
    assert "[REDACTED]" in reply.text
    # The card shows those same bytes and says a credential was redacted.
    assert "[REDACTED]" in card.text
    assert "shaped like a credential" in card.text
    assert f"reply {reply.id}" in card.text


async def test_announcing_costs_the_same_whether_there_are_five_tasks_or_one(
    db, workflows
):
    """It ran a count per task, every two seconds, for something that almost
    never has anything to do — twenty-one queries to usually find nothing.

    Both the pause lookup and the already-said lookup are in bulk, so the
    query count does not move with the batch."""
    queries: list[str] = []
    watched = ("tasks_in_state", "pauses_for", "announced")
    for name in watched:
        original = getattr(db, name)

        def counted(*a, _name=name, _original=original, **kw):
            queries.append(_name)
            return _original(*a, **kw)

        setattr(db, name, counted)

    for _ in range(5):
        task = await make_task(
            db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789"
        )
        await db.move_task(task.id, TaskState.NEEDS_HUMAN)

    await Pool(db=db, auto_ask=True).run_once()

    # One `tasks_in_state` for the pending sweep, one for the announcement.
    assert queries == ["tasks_in_state", "tasks_in_state", "pauses_for", "announced"]
    assert len(await db.outbound()) == 5


async def test_extraction_runs_when_a_message_is_linked(db):
    """End-to-end: a message is recorded, a task is linked to it, the runner
    reads the text, the registered extractor fills fields, the planner gets
    a complete Params and hands over. Without the link to original_text, the
    runner has nothing to extract from and the test would only prove the
    read path is plumbed.

    Run against `ops.request_permission`, not `backend.trace_problem`: ticket 6 replaced
    that type's node 0 with `Intake`, which makes no model call at all — the
    generic node-0-extraction mechanism this guards is no longer reachable
    through it.
    """
    from datetime import datetime

    from sqlalchemy import update as sa_update

    from friday.kernel.domain.messages import InboundEvent, MentionType
    from friday.kernel.extraction import _EXTRACTORS, build_extractor
    from friday.kernel.extraction.answer import answer_shape
    from friday.store import schema
    from plugins.ops.params import AccessRequestParams

    class StubResult:
        # The model has to repeat every field, including ones triage already
        # filled, because returning null would cancel triage's value. That is
        # what the extraction prompt instructs and the test mirrors.
        final_output = (
            '{"project": "payments repo", "permission": "write", "summary": null}'
        )

    prompts_seen: list[str] = []

    class StubHarness(ScriptedHarness):
        #: Subclassed rather than written from scratch, so `run_structured`
        #: — the validation and its correction turn — is the real one. A
        #: stub that supplied its own would let this pass while skipping the
        #: mechanism the extraction actually goes through.

        async def run(self, prompt, **kwargs):
            prompts_seen.append(prompt)
            return StubResult()

    ext = build_extractor(
        params_cls=AccessRequestParams,
        harness=StubHarness(answers=answer_shape(AccessRequestParams)),  # type: ignore[arg-type]
        name="request_permission_ext",
    )
    _EXTRACTORS["ops.request_permission"] = ext
    # Extraction happens inside the graph's own entry node — the same
    # `prepare` this stub extractor is wired into either way.

    try:
        event = InboundEvent(
            provider="fake",
            provider_message_id="m-ext-1",
            channel_id="watched",
            thread_id=None,
            author_id="u-reporter",
            author_name="reporter",
            text="cần quyền write vào repo payments",
            created_at=datetime.now(UTC),
            mention_type=MentionType.DIRECT,
        )
        await db.record_message(event)
        task = await make_request_permission(db)
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
        assert "payments" in prompts_seen[0]
        # And the workflow produced an outbound row of some kind, which is
        # how a task surfaces to a person.
        assert await db.outbound(), "no outbound produced"
    finally:
        _EXTRACTORS.pop("ops.request_permission", None)


async def test_ask_clarification_reaches_the_reporter_in_the_responders_words(db):
    """Ticket 05, end to end: the report already has a curl, so the
    structural floor (`_traceable`) finds nothing wrong — the only reason an
    `Ask` exists at all is the extractor naming a field in `ask_about` that
    code does not require. The sentence that reaches the reporter is the
    Responder's, not the template `_question_from_clarify` builds for it to
    write from.

    This mechanism — `prepare` node 0, an extractor's own `ask_about` beyond
    the structural floor — is generic, and `TraceProblemParams` is the one type
    with a field (`environment`) that is optional structurally but still has
    an `ask` phrase, which is what this needs. `backend.trace_problem`'s *real*
    graph no longer runs `prepare` at all (ticket 6: `Intake` replaced it), so
    this stands a `build_simple_dag` graph in for it — the same one-node
    shape every type with no investigation past node 0 already uses — rather
    than testing a mechanism the live graph does not have.
    """
    from sqlalchemy import update as sa_update

    from friday.kernel.config import AgentConfig
    from friday.kernel.dag.router import EDGE_ROUTER, build_simple_dag, register_dag
    from friday.kernel.domain.messages import InboundEvent, MentionType
    from friday.kernel.extraction import _EXTRACTORS, build_extractor
    from friday.kernel.extraction.answer import answer_shape
    from friday.kernel.harness.harness import Harness
    from friday.sdk.testing import ScriptedModel, function_call
    from friday.store import schema
    from plugins.backend.params import TraceProblemParams

    EDGE_ROUTER.pop("backend.trace_problem", None)
    register_dag(
        "backend.trace_problem",
        build_simple_dag("backend.trace_problem", TraceProblemParams),
    )

    ext = build_extractor(
        params_cls=TraceProblemParams,
        harness=Harness(
            config=AgentConfig(
                name="trace_problem_ext",
                api_key="k",
                base_url="https://example.invalid/v1",
                model="test-model",
            ),
            instructions="extract",
            answers=answer_shape(TraceProblemParams),
            model=ScriptedModel(
                [
                    [
                        function_call(
                            "answer",
                            {
                                "ask_about": ["environment"],
                                "because": "the curl doesn't say which server",
                            },
                            call_id="1",
                        )
                    ]
                ]
            ),
        ),
        name="trace_problem_ext",
    )
    _EXTRACTORS["backend.trace_problem"] = ext

    # Ticket 12: a draft naming no work, so this stays a test of whose
    # words reach the reporter rather than of `friday.kernel.responder.check`.
    responder = StubResponder("em đang chạy trên môi trường nào thế?")

    try:
        event = InboundEvent(
            provider="fake",
            provider_message_id="m-clarify-1",
            channel_id="watched",
            thread_id=None,
            author_id="u-reporter",
            author_name="reporter",
            text="checkout API bị lỗi rồi, curl -X GET /pay",
            created_at=datetime.now(UTC),
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
        _EXTRACTORS.pop("backend.trace_problem", None)
        EDGE_ROUTER.pop("backend.trace_problem", None)


async def test_the_responder_is_told_what_this_task_actually_knows(db):
    """Without it the model has only the conversation, and a conversation is a
    whole channel — it may hold another report's correlationId.

    Observed on the real provider: asked to request one, it read the channel,
    found one belonging to a different task, and wrote "ok có correlationId
    rồi, để anh trace thử". False, promising work nobody would do, and sent
    under the operator's name with no approval step.
    """
    from plugins.ops.params import AccessRequestParams

    responder = StubResponder("cho anh xin cái correlationId nhé")
    await make_request_permission(db, project="payments repo")

    await Pool(db=db, auto_ask=True, responder=responder).run_once()

    (given,) = responder.given_params
    assert isinstance(given, AccessRequestParams)
    assert given.project == "payments repo"
    assert given.permission == "", "it must be able to see what is absent"


# --- a burst is one thought sent in three messages ---------------------------


async def _said(db, message_id, text, *, secs, mention=None, author="u-reporter"):
    from datetime import timedelta

    from friday.kernel.domain.messages import InboundEvent, MentionType

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


BURST_START = datetime(2026, 9, 2, 3, 0, tzinfo=UTC)


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
        make_event(message_id="m1"), task.id, decision={"type": "backend.trace_problem"}
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
        make_event(message_id="m1"), task.id, decision={"type": "backend.trace_problem"}
    )
    await _said(db, "m2", "trưa nay ăn gì mọi người", secs=3, author="someone-else")

    assert "trưa nay" not in (await db.original_text_for(task.id) or "")


async def test_what_we_posted_is_not_read_back(db):
    """With `capture_own_messages` on, the operator is both the reporter and
    the account, so "same author" would otherwise feed our own questions back
    into the extractor."""
    from friday.kernel.outbox import Kind

    task = await make_task(db)
    await _said(db, "m1", "@Lee API lỗi rồi", secs=0, mention=True, author="me")
    await db.mark_triaged(
        make_event(message_id="m1"), task.id, decision={"type": "backend.trace_problem"}
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
        make_event(message_id="m1"), task.id, decision={"type": "backend.trace_problem"}
    )

    assert "hôm qua deploy" not in (await db.original_text_for(task.id) or "")


# --- ticket 08: the build respects a budget ---------------------------------


async def test_an_unset_budget_changes_nothing(db):
    """D7: the default, and the behaviour every install had before this
    ticket — bounded by the message count alone."""
    task = await make_task(db)
    await _said(db, "m1", "@Lee API lỗi rồi a ơi", secs=0, mention=True)
    await db.mark_triaged(
        make_event(message_id="m1"), task.id, decision={"type": "backend.trace_problem"}
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
        make_event(message_id="m1"), task.id, decision={"type": "backend.trace_problem"}
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
        make_event(message_id="m1"), task.id, decision={"type": "backend.trace_problem"}
    )
    await _said(
        db, "m2", "correlationId là abcdef01-2345-6789-abcd-ef0123456789", secs=3
    )

    said = await db.original_text_for(task.id, budget_tokens=20)

    assert "API lỗi rồi" not in said, "the oldest message should have been dropped"
    assert "abcdef01-2345-6789-abcd-ef0123456789" in said, (
        "the newest message must survive the drop"
    )


async def test_a_budget_the_transcript_already_fits_changes_nothing(db):
    task = await make_task(db)
    await _said(db, "m1", "@Lee API lỗi rồi", secs=0, mention=True)
    await db.mark_triaged(
        make_event(message_id="m1"), task.id, decision={"type": "backend.trace_problem"}
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
        make_event(message_id="m1"), task.id, decision={"type": "backend.trace_problem"}
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

    from friday.kernel.domain.messages import InboundEvent

    await db.record_message(
        InboundEvent(
            provider="fake",
            provider_message_id=message_id,
            channel_id="watched",
            thread_id=None,
            author_id=author,
            author_name="Long",
            text=text,
            created_at=datetime.now(UTC) + timedelta(seconds=secs),
            mention_type=None,
            is_own=True,
            reply_to=reply_to,
        ),
        context_only=True,
    )


async def test_the_operator_answering_closes_the_task_and_withdraws_the_draft(db):
    """A `reply` waits for approval with no expiry. Without this, approving it
    two days later sends an answer that stopped being true when they typed."""
    from friday.kernel.domain.states import OutboundState, TaskState
    from friday.kernel.outbox import Kind

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
    from friday.kernel.outbox import Kind

    task = await make_request_permission(db)
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


async def test_with_several_open_tasks_and_no_reply_nothing_closes(db, workflows):
    """Guessing which one they meant loses work. When it is not clear, the
    answer is a person, not a guess."""
    await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
    await make_task(db, curl="curl -X GET /pay")
    await _operator_said(db, "op-1", "để anh xem")

    await Pool(db=db, auto_ask=False).run_once()

    states = {t.id: t.state for t in await db.tasks()}
    assert "handled_by_operator" not in states.values()


async def test_a_reply_picks_the_task_out_of_several(db):
    """Their reply names what it answers — the reporter's message, which is
    linked to a task."""
    from friday.kernel.domain.states import TaskState

    a = await make_request_permission(db)
    b = await make_request_permission(db)
    await db.record_message(make_event(message_id="report-b", text="curl lỗi"))
    await db.mark_triaged(
        make_event(message_id="report-b"),
        b.id,
        decision={"type": "ops.request_permission"},
    )
    await _operator_said(db, "op-1", "cái curl đó thiếu header", reply_to="report-b")

    await Pool(db=db, auto_ask=False).run_once()

    states = {t.id: t.state for t in await db.tasks()}
    assert states[b.id] == TaskState.HANDLED_BY_OPERATOR
    assert states[a.id] != TaskState.HANDLED_BY_OPERATOR


async def test_a_handled_task_can_be_reopened_by_a_person(db):
    """Closing on "they said something in this channel" will sometimes be
    wrong, so it cannot be terminal."""
    from friday.kernel.domain.states import TaskState

    task = await make_task(db)
    await db.move_task(task.id, TaskState.HANDLED_BY_OPERATOR)
    await db.move_task(task.id, TaskState.PENDING)

    assert (await db.tasks())[0].state == TaskState.PENDING


# --- ticket 41: a stranger changes the pronouns, and nothing else ------------


async def test_someone_the_operator_never_wrote_to_is_a_stranger(db):
    responder = StubResponder("dạ anh/chị gửi mình correlationId nhé")
    task = await make_request_permission(db)
    await db.record_message(make_event(message_id="m1", author_id="newcomer"))
    await db.mark_triaged(
        make_event(message_id="m1", author_id="newcomer"),
        task.id,
        decision={"type": "ops.request_permission"},
    )

    await Pool(db=db, auto_ask=True, responder=responder).run_once()

    assert responder.strangers == [True]


async def test_an_exchange_in_either_direction_makes_them_known(db):
    """A reply is the smallest thing that is an actual exchange. Being in the
    same channel is not — the operator has spoken in every watched channel."""
    responder = StubResponder("cho anh xin correlationId nhé")
    task = await make_request_permission(db)
    await db.record_message(make_event(message_id="m1", author_id="dana"))
    await db.mark_triaged(
        make_event(message_id="m1", author_id="dana"),
        task.id,
        decision={"type": "ops.request_permission"},
    )
    # Long once replied to something dana said.
    await db.record_message(
        make_event(message_id="old", author_id="dana", text="hi"), context_only=True
    )
    await db.record_message(
        make_event(
            message_id="long-said",
            author_id="me",
            is_own=True,
            text="hi em",
            reply_to="old",
        ),
        context_only=True,
    )

    await Pool(db=db, auto_ask=True, responder=responder).run_once()

    assert responder.strangers == [False]


async def test_being_written_down_for_the_room_makes_them_known(db):
    """Written down is a `person` row keyed on their Discord id — a name in a
    channel file's `people:` map until the files went (board
    `read-it-the-way-the-operator-does`, ticket 10)."""
    from friday.kernel.domain.state import FridayState
    from friday.sdk.memory import MemoryOrigin

    await db.memory_add(
        FridayState(channel_id="watched", agent="operator"),
        "",
        kind="person",
        origin=MemoryOrigin.ADMIN,
        data={"discord_id": "dana", "name": "Dana", "role": "qa", "team": "orders"},
    )
    responder = StubResponder("ok")
    task = await make_request_permission(db)
    await db.record_message(make_event(message_id="m1", author_id="dana"))
    await db.mark_triaged(
        make_event(message_id="m1", author_id="dana"),
        task.id,
        decision={"type": "ops.request_permission"},
    )

    await Pool(db=db, auto_ask=True, responder=responder).run_once()

    assert responder.strangers == [False]


async def test_the_stranger_line_reaches_the_prompt_and_only_then(tmp_path):
    from friday.kernel.config import AgentConfig
    from friday.kernel.responder import Responder
    from friday.sdk.testing import FunctionModel

    prompts: list[str] = []

    def capture(messages, info):
        shown = [
            part.content
            for message in messages
            for part in getattr(message, "parts", [])
            if getattr(part, "part_kind", "") == "user-prompt"
            and isinstance(part.content, str)
        ]
        prompts.append("\n".join(shown))
        raise RuntimeError("captured")

    responder = Responder(
        config=AgentConfig(name="r", api_key="k", base_url="http://x/v1", model="m"),
        model=FunctionModel(capture, model_name="test-model"),
    )
    await responder.draft(asking="q", stranger=True)
    await responder.draft(asking="q", stranger=False)

    assert "not written to this person" in prompts[0]
    assert "counterpart" not in prompts[1]


# --- ticket 35: tell the operator what they were asked -----------------------


async def _reporter_said(db, message_id, text, *, secs, task_id=None, reply_to=None):
    from datetime import timedelta

    event = make_event(
        message_id=message_id,
        text=text,
        mention_type=None,
        reply_to=reply_to,
        created_at=datetime(2026, 9, 2, 3, 0, tzinfo=UTC) + timedelta(seconds=secs),
    )
    await db.record_message(event, context_only=task_id is None)
    if task_id is not None:
        await db.mark_triaged(
            event, task_id, decision={"type": "backend.trace_problem"}
        )


async def test_the_operator_is_told_what_the_reporter_asked(db):
    """The announcement was the type and the parameters — all true, none of it
    the reason the task stopped. The reporter asked what a correlationId is;
    the operator saw a task with one in it and no hint that a person was
    waiting on a sentence they could type in five seconds."""
    task = await make_task(db)
    await _reporter_said(db, "m1", "API lỗi rồi", secs=0, task_id=task.id)
    await _reporter_said(
        db, "m2", "correlationId là cái gì a nhỉ?", secs=30, reply_to="m1"
    )
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
    from friday.kernel.outbox import Kind

    task = await make_task(db)
    await _reporter_said(db, "m1", "API lỗi rồi", secs=0, task_id=task.id)
    row = await db.queue_outbound(
        task_id=task.id,
        conversation=task.conversation,
        kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user",
        text="cho anh xin correlationId",
    )
    await db.mark_outbound_sent(row.id, sent_message_id="ours")
    await _reporter_said(
        db, "ours", "cho anh xin correlationId", secs=10, reply_to="m1"
    )
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


async def test_the_calls_a_task_causes_are_stamped_with_that_task(db):
    """The reason `task_id` exists, driven where it actually happens.

    An extractor runs inside a task's graph, on every pass, against every
    message the reporter has sent — so the message a call was "about" is not a
    question with one answer, and until now those rows landed under no key at
    all. This is the only test that would notice the chain from `Pool` through
    `prepare_node` to the extractor's `Harness` coming apart in the middle,
    which is exactly how the `record=` chain broke one ticket ago.

    Stands a `build_simple_dag` graph in for `backend.trace_problem`'s real one,
    the same way the clarification test beside this one does: that graph's
    node 0 is `Intake` now (ticket 6), which never calls `prepare` or an
    extractor, so the chain this guards is not reachable through it any more.
    """
    from datetime import datetime

    from sqlalchemy import update as sa_update

    from friday.kernel.config import AgentConfig
    from friday.kernel.dag.router import EDGE_ROUTER, build_simple_dag, register_dag
    from friday.kernel.domain.messages import InboundEvent, MentionType
    from friday.kernel.extraction import _EXTRACTORS, build_extractor
    from friday.kernel.extraction.answer import answer_shape
    from friday.kernel.harness.harness import Harness
    from friday.sdk.testing import ScriptedModel, assistant_message
    from friday.store import schema
    from plugins.backend.params import TraceProblemParams

    EDGE_ROUTER.pop("backend.trace_problem", None)
    register_dag(
        "backend.trace_problem",
        build_simple_dag("backend.trace_problem", TraceProblemParams),
    )

    recorded: list = []

    async def sink(call) -> None:
        recorded.append(call)

    # Deliberately incomplete: a complete report ends the graph without ever
    # asking, and the ask is what reaches the responder — the *other* agent
    # that stamps a task id, and the one that writes text a person reads.
    filled = '{"summary": "checkout 500", "environment": "production", "curl": null}'
    ext = build_extractor(
        params_cls=TraceProblemParams,
        harness=Harness(
            config=AgentConfig(
                name="trace_problem_extractor",
                api_key="k",
                base_url="https://example.invalid/v1",
                model="test-model",
            ),
            instructions="lift the fields out",
            answers=answer_shape(TraceProblemParams),
            model=ScriptedModel([[assistant_message(filled)]]),
            record=sink,
        ),
        name="trace_problem_extractor",
    )
    kept = _EXTRACTORS.get("backend.trace_problem")
    _EXTRACTORS["backend.trace_problem"] = ext

    try:
        await db.record_message(
            InboundEvent(
                provider="fake",
                provider_message_id="m-1",
                channel_id="watched",
                thread_id=None,
                author_id="u",
                author_name="reporter",
                text="production broke at noon",
                created_at=datetime.now(UTC),
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
            _EXTRACTORS.pop("backend.trace_problem", None)
        else:
            _EXTRACTORS["backend.trace_problem"] = kept
        EDGE_ROUTER.pop("backend.trace_problem", None)

    by_agent = {c.agent: c for c in recorded}
    assert set(by_agent) == {"trace_problem_extractor", "responder"}, (
        "both legs of the chain, because either can come apart on its own"
    )
    assert by_agent["trace_problem_extractor"].task_id == task.id
    assert by_agent["trace_problem_extractor"].node == "prepare"
    assert by_agent["trace_problem_extractor"].message_id is None, (
        "an extractor reads a task, not one message"
    )
    assert by_agent["responder"].task_id == task.id
    assert by_agent["responder"].node is None, "a responder is not a graph node"


def _responder(sink):
    """A real `Responder` over a scripted model, so the whole path from
    `Pool` to `Harness` is exercised rather than stubbed at the first joint."""
    from friday.kernel.config import AgentConfig
    from friday.kernel.responder import Responder
    from friday.sdk.testing import ScriptedModel, assistant_message

    return Responder(
        config=AgentConfig(
            name="responder",
            api_key="k",
            base_url="https://example.invalid/v1",
            model="test-model",
        ),
        model=ScriptedModel([[assistant_message("cho anh xin correlationId nhé")]]),
        record=sink,
    )


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
    await make_request_permission(db)
    responder = StubResponder("ok có correlationId rồi, để anh trace thử")

    await Pool(db=db, auto_ask=True, responder=responder).run_once()

    (queued,) = await db.outbound()
    assert "để anh trace" not in queued.text
    assert queued.text == responder.asked[0], "the template, unchanged"
    assert "project" in queued.text


async def test_a_draft_that_only_reworded_the_question_does_reach_them(db):
    """The wording is allowed to change — that is the whole reason the
    responder is asked. A floor that only accepted the template would make the
    step pointless."""
    await make_request_permission(db)
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
        created_at=datetime.now(UTC) + timedelta(seconds=secs),
    )
    await db.record_message(event)
    await db.mark_triaged(event, task_id, decision={"type": "backend.trace_problem"})


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
    task = await make_request_permission(db)
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
# `trace_problem` meant five minutes in which nothing else was asked, drafted or
# handed over.


def _graph(task_type, node):
    from friday.kernel.dag.router import EDGE_ROUTER, register_dag
    from friday.sdk.workflow import DAG, Node

    EDGE_ROUTER.pop(task_type, None)
    register_dag(task_type, DAG(name=f"{task_type}-test", nodes=(Node("only", node),)))


async def _doc_task(db):
    return await db.create_task(
        conversation=ConversationId("fake", "watched"),
        type="backend.answer_question",
        state="pending",
        confidence=0.9,
        params={"question": "?"},
    )


async def test_a_quick_graph_is_not_held_behind_a_slow_one(db):
    """The slow graph is first in the batch and cannot finish until the quick
    one has — so a pool that awaits them in turn never finishes at all."""
    import asyncio

    from friday.sdk.actions import HandOver

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

    _graph("backend.trace_problem", slow)
    _graph("backend.answer_question", quick)
    await make_task(db)
    await _doc_task(db)

    acted = await asyncio.wait_for(Pool(db=db, auto_ask=True).run_once(), 5)

    assert finished == ["quick", "slow"]
    assert len(acted) == 2


async def test_no_more_graphs_run_at_once_than_configured(db):
    import asyncio

    from friday.sdk.actions import HandOver

    running = 0
    most = 0

    async def counted(state, deps):
        nonlocal running, most
        running += 1
        most = max(most, running)
        await asyncio.sleep(0.02)
        running -= 1
        return HandOver("done")

    _graph("backend.trace_problem", counted)
    for _ in range(4):
        await make_task(db)

    acted = await Pool(db=db, auto_ask=True, concurrency=2).run_once()

    assert len(acted) == 4
    assert most == 2


async def test_one_task_is_never_acted_on_twice_at_once(db):
    """A task stays `pending` for as long as its graph runs, so a second pass
    that starts meanwhile reads it as work waiting. It must leave it alone."""
    import asyncio

    from friday.sdk.actions import HandOver

    entered = 0
    inside = asyncio.Event()
    release = asyncio.Event()

    async def held(state, deps):
        nonlocal entered
        entered += 1
        inside.set()
        await release.wait()
        return HandOver("done")

    _graph("backend.trace_problem", held)
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

    from friday.sdk.actions import HandOver

    running = 0
    most = 0

    async def counted(state, deps):
        nonlocal running, most
        running += 1
        most = max(most, running)
        await asyncio.sleep(0.02)
        running -= 1
        return HandOver("done")

    _graph("backend.trace_problem", counted)
    for _ in range(4):
        await make_task(db)
    config = SimpleNamespace(
        workflows=SimpleNamespace(auto_ask_for_details=True, max_asks=3, concurrency=3)
    )

    await Pool.build(config, db=db).run_once()

    assert most == 3
