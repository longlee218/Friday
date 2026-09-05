"""Ticket 04 slice 4 — applying triage decisions.

Triage decides; this applies. Kept apart so the decision is testable without a
database and the application is testable without a model.
"""

from __future__ import annotations

from conftest import captured, make_event
from datetime import datetime, timedelta, timezone

from friday.domain.conversation import ConversationId
from friday.triage import Decided, NeedsHuman
from friday.triage.runner import TriageRunner
from friday.domain.states import TaskState


class StubTriage:
    """Returns preset outcomes. The runner's seam is the decision, not the model."""

    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.seen = []

    async def decide(self, event, *, context=(), calls=None):
        self.seen.append(event)
        return self._outcomes.pop(0) if self._outcomes else NeedsHuman("no script")


def api_issue(confidence=0.9):
    return Decided(type="api_issue", confidence=confidence)


def runner(db, triage, threshold=0.7):
    return TriageRunner(db=db, triage=triage, confidence_threshold=threshold)


async def test_a_confident_decision_becomes_a_task(inbox, provider, db):
    provider.emit(make_event(message_id="10"))
    await captured(inbox)

    created = await runner(db, StubTriage(api_issue())).run_once()

    assert [t.type for t in created] == ["api_issue"]
    stored = await db.tasks()
    # Opened empty: triage said what it is and nothing more. The extractor
    # fills it in on the first plan, and until then there is nothing here to
    # be wrong.
    assert stored[0].params == {}
    assert stored[0].state == "pending"


async def test_a_skip_creates_no_task(inbox, provider, db):
    provider.emit(make_event(message_id="10"))
    await captured(inbox)
    skip = Decided(type="skip", confidence=0.99)

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


async def test_a_message_of_a_different_type_gets_its_own_task(inbox, provider, db):
    """A bug report that turns into something else must not be relabelled
    silently — and the new message must not be swallowed saying so.

    It used to be: the open task was flagged for a human and the new message
    was marked triaged against it, so no task was ever opened for what it
    actually was. Caught by running the real pipeline over six messages — a
    genuine api_issue arrived after a held one and produced nothing at all.
    """
    provider.emit(make_event(message_id="10"))
    provider.emit(make_event(message_id="20", text="actually give me repo access"))
    await captured(inbox)
    access = Decided(type="access_request", confidence=0.9)

    r = runner(db, StubTriage(api_issue(), access))
    await r.run_once()

    tasks = await db.tasks()
    assert [(t.type, t.state) for t in tasks] == [
        ("api_issue", "needs_human"),   # flagged: its subject changed
        ("access_request", "pending"),  # and the new report is real work
    ]


async def test_triage_reads_the_conversations_context(inbox, provider, db):
    provider.emit_recent("watched", make_event(message_id="1", text="deploy went out"))
    provider.emit(make_event(message_id="10"))
    await captured(inbox)
    triage = StubTriage(api_issue())

    await runner(db, triage).run_once()

    assert triage.seen  # and it was given the conversation, see runner


async def test_a_follow_up_sends_the_task_back_to_be_re_planned(inbox, provider, db):
    """The answer to "which environment?" arrives as an ordinary message.

    Triage used to lift the values out of it — the second place it extracted.
    It cannot now, and it does not have to: the follow-up is linked to the
    task, the task goes back to pending, and the extractor reads everything the
    reporter has said. What matters here is only that the answer wakes the task
    up. If it does not, the task waits forever for something it has been told.
    """
    provider.emit(make_event(message_id="10"))
    await captured(inbox)
    await runner(db, StubTriage(api_issue())).run_once()
    task_id = (await db.tasks())[0].id
    await db.move_task(task_id, TaskState.WAITING_FOR_DETAILS)

    provider.emit(make_event(message_id="20", text="prod, correlationId abc-123"))
    await captured(inbox)
    await runner(db, StubTriage(api_issue())).run_once()

    assert (await db.tasks())[0].state == "pending"


async def test_the_extractor_is_shown_the_answer_and_not_only_the_report(
    inbox, provider, db
):
    """Which is what makes the above enough. Every message linked to the task
    is handed to the extractor, oldest first — the correlationId arrives in the
    second one, and nothing else in the system reads it."""
    provider.emit(make_event(message_id="10", text="API lỗi nè"))
    await captured(inbox)
    await runner(db, StubTriage(api_issue())).run_once()
    task_id = (await db.tasks())[0].id

    provider.emit(make_event(message_id="20", text="correlationId abc-123 nhé"))
    await captured(inbox)
    await runner(db, StubTriage(api_issue())).run_once()

    said = await db.original_text_for(task_id)
    assert "API lỗi nè" in said
    assert "abc-123" in said

async def test_a_skip_is_still_recorded_as_a_decision(inbox, provider, db):
    """A skip creates no task, so without this the decision leaves no trace and
    the confidence threshold can never be checked against real messages."""
    provider.emit(make_event(message_id="10"))
    await captured(inbox)
    skip = Decided(type="skip", confidence=0.95)

    await runner(db, StubTriage(skip)).run_once()

    (decision,) = await db.decisions()
    assert decision["type"] == "skip"
    assert decision["confidence"] == 0.95
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


# --- ticket 34: a reply belongs to the task it answers -----------------------


async def _we_asked(db, task_id: int, *, sent_as: str) -> None:
    """The question we sent about a task, and the id it became."""
    from friday.outbox import Kind

    row = await db.queue_outbound(
        task_id=task_id,
        conversation=ConversationId("fake", "watched"),
        kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user",
        text="cho anh xin cái correlationId nhé",
    )
    await db.mark_outbound_sent(row.id, sent_message_id=sent_as)


async def test_a_question_back_does_not_close_the_report(inbox, provider, db):
    """The thread this ticket came from, with triage doing the thing it is
    entitled to do:

        them:  a Long ơi a kiểm tra API giúp e e thấy bị 500
        us:    em gửi anh cái correlationId hoặc curl em gọi được không?
        them:  correlationId là cái gì a nhỉ, e ko biết   → doc_question

    Live, triage called that third message `api_issue` and everything worked.
    It is not obliged to. Called `doc_question` it used to push the report to
    `needs_human` and open a second task — for a question that is about the
    first one.
    """
    provider.emit(make_event(message_id="10"))
    await captured(inbox)
    await runner(db, StubTriage(api_issue())).run_once()
    task_id = (await db.tasks())[0].id
    await _we_asked(db, task_id, sent_as="ours-1")
    await db.move_task(task_id, TaskState.WAITING_FOR_DETAILS)

    provider.emit(
        make_event(
            message_id="20",
            text="correlationId là cái gì a nhỉ",
            mention_type=None,
            reply_to="ours-1",
        )
    )
    await captured(inbox)
    other = Decided(type="doc_question", confidence=0.9)
    await runner(db, StubTriage(other)).run_once()

    tasks = await db.tasks()
    assert [(t.type, t.state) for t in tasks] == [("api_issue", "pending")]


async def test_the_answer_reaches_the_task_that_asked_for_it(inbox, provider, db):
    """Same rule, the case it exists for: they answer, and the answer is worked
    as part of the task that asked — not as a report of its own."""
    provider.emit(make_event(message_id="10"))
    await captured(inbox)
    await runner(db, StubTriage(api_issue())).run_once()
    task_id = (await db.tasks())[0].id
    await _we_asked(db, task_id, sent_as="ours-1")
    await db.move_task(task_id, TaskState.WAITING_FOR_DETAILS)

    provider.emit(
        make_event(
            message_id="20",
            text="đây a: abcdef01-2345-6789-abcd-ef0123456789",
            mention_type=None,
            reply_to="ours-1",
        )
    )
    await captured(inbox)
    await runner(db, StubTriage(Decided(type="access_request", confidence=0.9))).run_once()

    assert [(t.type, t.state) for t in await db.tasks()] == [("api_issue", "pending")]


async def test_an_unprompted_message_is_still_checked_by_type(inbox, provider, db):
    """This narrows when the type check applies; it does not remove it. A
    conversation that drifts into something else must still not be relabelled
    without somebody noticing."""
    provider.emit(make_event(message_id="10"))
    provider.emit(make_event(message_id="20", text="actually give me repo access"))
    await captured(inbox)
    access = Decided(type="access_request", confidence=0.9)

    await runner(db, StubTriage(api_issue(), access)).run_once()

    assert [(t.type, t.state) for t in await db.tasks()] == [
        ("api_issue", "needs_human"),
        ("access_request", "pending"),
    ]


async def test_a_reply_to_a_message_of_ours_that_had_no_task(inbox, provider, db):
    """The daily summary and an outage alert belong to no task. Replying to one
    falls back to the ordinary path rather than erroring."""
    from friday.outbox import Kind

    row = await db.queue_outbound(
        task_id=None,
        conversation=ConversationId("fake", "watched"),
        kind=Kind.ALERT,
        sender="discord_bot",
        text="Alive. 27 messages held.",
    )
    await db.mark_outbound_sent(row.id, sent_message_id="alert-1")

    provider.emit(make_event(message_id="10", text="ok anh", reply_to="alert-1"))
    await captured(inbox)

    await runner(db, StubTriage(api_issue())).run_once()

    assert [t.type for t in await db.tasks()] == ["api_issue"]


async def test_a_reply_does_not_reopen_finished_work(inbox, provider, db):
    """A reply is not a reason to reopen work somebody closed."""
    provider.emit(make_event(message_id="10"))
    await captured(inbox)
    await runner(db, StubTriage(api_issue())).run_once()
    task_id = (await db.tasks())[0].id
    await _we_asked(db, task_id, sent_as="ours-1")
    await db.move_task(task_id, TaskState.REVIEW)
    await db.move_task(task_id, TaskState.DONE)

    provider.emit(
        make_event(message_id="20", text="cảm ơn anh", reply_to="ours-1")
    )
    await captured(inbox)
    await runner(db, StubTriage(api_issue())).run_once()

    states = [(t.type, t.state) for t in await db.tasks()]
    assert ("api_issue", "done") in states
    assert len(states) == 2, "the thank-you opened its own task rather than reopening"


# --- ticket 38: a turn, not a message ----------------------------------------


def _typing(answer: bool):
    return lambda conversation, author_id: answer


async def test_three_messages_in_five_seconds_are_classified_once(inbox, provider, db):
    """People send one thought in three messages. Only the first carries a
    mention; the others are stored as context. Triage used to classify the
    mention alone, so the curl in the second message never reached the task and
    the system asked for what it had just been sent."""
    from datetime import timedelta

    t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
    provider.emit(make_event(message_id="1", text="@Lee API lỗi rồi a ơi", created_at=t0))
    provider.emit(
        make_event(
            message_id="2",
            text="curl -X POST /pay trả 500",
            mention_type=None,
            created_at=t0 + timedelta(seconds=3),
        )
    )
    provider.emit(
        make_event(
            message_id="3",
            text="trên production nhé",
            mention_type=None,
            created_at=t0 + timedelta(seconds=5),
        )
    )
    await captured(inbox)
    triage = StubTriage(api_issue())

    await runner(db, triage).run_once()

    (seen,) = triage.seen
    assert "API lỗi rồi" in seen.text
    assert "curl -X POST /pay" in seen.text
    assert "trên production" in seen.text
    assert len(await db.tasks()) == 1


async def test_a_turn_still_open_is_left_for_the_next_pass(inbox, provider, db):
    """Twelve seconds of silence means they have stopped. Three seconds does
    not."""
    provider.emit(make_event(message_id="1", created_at=datetime.now(timezone.utc)))
    await captured(inbox)
    triage = StubTriage(api_issue())
    r = TriageRunner(db=db, triage=triage, confidence_threshold=0.7, turn_seconds=12)

    await r.run_once()

    assert triage.seen == [], "classified a turn that might not be finished"
    assert await db.tasks() == []


async def test_typing_keeps_a_turn_open_past_the_window(inbox, provider, db):
    from datetime import timedelta

    old = datetime.now(timezone.utc) - timedelta(seconds=30)
    provider.emit(make_event(message_id="1", created_at=old))
    await captured(inbox)
    triage = StubTriage(api_issue())
    r = TriageRunner(
        db=db, triage=triage, confidence_threshold=0.7,
        turn_seconds=12, still_typing=_typing(True),
    )

    await r.run_once()

    assert triage.seen == [], "they are still typing"


async def test_somebody_else_speaking_closes_the_turn_at_once(inbox, provider, db):
    """No waiting out the window: the floor has changed hands."""
    now = datetime.now(timezone.utc)
    provider.emit(make_event(message_id="1", text="@Lee API lỗi", created_at=now))
    provider.emit(
        make_event(
            message_id="2", text="ừ mình cũng thấy", author_id="u-other",
            author_name="minh", mention_type=None, created_at=now,
        )
    )
    await captured(inbox)
    triage = StubTriage(api_issue())
    r = TriageRunner(
        db=db, triage=triage, confidence_threshold=0.7,
        turn_seconds=12, still_typing=_typing(True),
    )

    await r.run_once()

    (seen,) = triage.seen
    assert seen.text == "@Lee API lỗi"
    assert "mình cũng thấy" not in seen.text


async def test_what_this_process_posted_is_not_part_of_their_turn(inbox, provider, db):
    """In the channel the operator tests in, the account is both sides."""
    from datetime import timedelta

    from friday.outbox import Kind

    t0 = datetime.now(timezone.utc) - timedelta(seconds=60)
    row = await db.queue_outbound(
        task_id=None, conversation=ConversationId("fake", "watched"),
        kind=Kind.ASK_FOR_DETAILS, sender="discord_user", text="cho anh xin correlationId",
    )
    await db.mark_outbound_sent(row.id, sent_message_id="ours")
    provider.emit(make_event(message_id="1", text="@Lee API lỗi", created_at=t0))
    provider.emit(
        make_event(
            message_id="ours", text="cho anh xin correlationId", mention_type=None,
            created_at=t0 + timedelta(seconds=2),
        )
    )
    await captured(inbox)
    triage = StubTriage(api_issue())

    await runner(db, triage).run_once()

    (seen,) = triage.seen
    assert "cho anh xin" not in seen.text


# --- a message we sent must not wedge the queue -----------------------------


async def _one_of_ours_in_the_queue(db, *, text="Alive. 79 messages held."):
    """The production shape: a message this process posted, sitting untriaged.

    The bot DMs the operator; that DM comes back through the *user* gateway,
    where `is_own` is decided against the user account and so reads False for
    anything the bot wrote. A DM bypasses the channel whitelist, so it lands
    in the queue as work.
    """
    from friday.outbox import Kind

    task = await db.create_task(
        conversation=ConversationId("fake", "watched"),
        type="api_issue", state="pending", confidence=0.9, params={},
    )
    row = await db.queue_outbound(
        task_id=task.id, conversation=task.conversation, kind=Kind.HELP_WANTED,
        sender="discord_bot", text=text,
    )
    await db.mark_outbound_sent(row.id, sent_message_id="ours-echo")
    await db.record_message(
        make_event(
            message_id="ours-echo", text=text, author_id="bot",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
    )
    return task


async def test_a_message_we_sent_does_not_take_the_process_down(db):
    """`turn_from` excludes anything we posted, so the turn of a message we
    posted is *empty* — and empty with `closed_by_someone_else` False, which
    is the one combination `_still_open` was never written for. `turn[-1]`
    raised `IndexError` out of `run_forever`, inside the TaskGroup, taking
    ingest, the outbox, the board and the heartbeat down with it.

    Worse than a crash: the row is never marked triaged, so it stays at the
    head of the queue and every restart dies on it. A boot loop.
    """
    await _one_of_ours_in_the_queue(db)
    triage = StubTriage()

    acted = await runner(db, triage).run_once()

    assert acted == [], "a message we sent is not work"
    assert triage.seen == [], "and it never reaches the model"
    assert await db.untriaged_mentions() == [], (
        "it must leave the queue, or the next pass finds it again for ever"
    )


async def test_one_unreadable_message_does_not_stop_the_others(db):
    """Per-message isolation. Whatever else goes wrong with one row, the rest
    of the batch is still triaged and the loop still comes back."""
    await _one_of_ours_in_the_queue(db)
    real = make_event(message_id="20", text="API lỗi rồi anh ơi")
    await db.record_message(real)

    acted = await runner(db, StubTriage(api_issue())).run_once()

    assert [t.type for t in acted] == ["api_issue"], (
        "the good message was triaged despite the bad one ahead of it"
    )


async def test_a_mention_is_never_lost_to_a_spent_budget(db, provider, inbox):
    """A refusal is not a discard.

    The ceiling stops a call from happening; it must not stop the mention from
    being anybody's problem. `docs/DESIGN.md`'s never-drop rule has no
    exception for running out of money — an unclassified mention that nobody
    is told about is indistinguishable from correct operation, which is the
    failure this whole system is shaped against.
    """
    from dataclasses import replace as _replace

    from agents.models.interface import Model

    from friday.config import AgentConfig
    from friday.triage import Triage
    from friday.triage.runner import NEEDS_HUMAN, TriageRunner

    class NeverReached(Model):
        async def get_response(self, *a, **kw):
            raise AssertionError("the provider was called despite the ceiling")

        def stream_response(self, *a, **kw):
            raise NotImplementedError

    config = AgentConfig(
        name="triage", api_key="k", base_url="https://example.invalid/v1",
        model="test-model", daily_token_budget=10,
    )
    provider.emit(make_event(message_id="10", text="checkout is 500ing"))
    await captured(inbox)

    await TriageRunner(
        db=db,
        triage=Triage(
            config=config, model=NeverReached(), spent=lambda agent: _spent(999)
        ),
        confidence_threshold=0.7,
    ).run_once()

    (task,) = await db.tasks_in_state(NEEDS_HUMAN, 10)
    assert "tokens today" in task.params["reason"]


async def _spent(n: int) -> int:
    return n
