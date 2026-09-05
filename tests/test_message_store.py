"""Ticket 11 — one message table.

Everything the system has seen is one kind of thing. What separates the triage
queue from the surrounding context is a column, not a second table.
"""

from __future__ import annotations

from conftest import FakeProvider, captured, make_event
from friday.domain.conversation import ConversationId
from friday.config import IngestConfig
from friday.inbox import Inbox
from friday.domain.models import MentionType
from friday.domain.states import TaskState


def context(**kw):
    """Someone talking in the channel without addressing us."""
    return make_event(mention_type=None, **kw)


async def test_a_mention_and_its_context_live_in_one_store(inbox, provider, db):
    provider.emit_recent("watched", context(message_id="1", text="deploy went out"))
    provider.emit(make_event(message_id="10", text="api is wrong"))
    await captured(inbox)

    assert {m.text for m in await db.messages()} == {"deploy went out", "api is wrong"}
    assert [m.text for m in await db.mentions()] == ["api is wrong"]


async def test_only_mentions_are_queued_for_triage(inbox, provider, db):
    """Context is stored, but nobody classifies it."""
    provider.emit_recent("watched", context(message_id="1", text="deploy went out"))
    provider.emit(make_event(message_id="10", text="api is wrong"))
    await captured(inbox)

    assert [m.text for m in await db.untriaged_mentions()] == ["api is wrong"]


async def test_an_old_mention_pulled_in_as_context_is_not_queued(inbox, provider, db):
    """Seeding reaches back to before the conversation involved us. A mention
    found back there is history — classifying it would open a task for
    something already dealt with weeks ago."""
    provider.emit_recent("watched", make_event(message_id="1", text="tagged you then"))
    provider.emit(make_event(message_id="10", text="api is wrong"))
    await captured(inbox)

    assert {m.text for m in await db.mentions()} == {"tagged you then", "api is wrong"}
    assert [m.text for m in await db.untriaged_mentions()] == ["api is wrong"]


async def test_a_message_in_a_thread_comes_back_as_a_thread_message(
    inbox, provider, db
):
    """The old two-table split rebuilt every stored message as a channel
    message with no thread, because only one of the two tables carried it."""
    provider.emit(make_event(message_id="10", channel_id="watched", thread_id="t1"))
    await captured(inbox)

    (stored,) = await db.messages()
    assert stored.channel_id == "watched"
    assert stored.thread_id == "t1"
    assert stored.conversation == ConversationId("fake", "watched", "t1")


# The migration from the old two-table shape lived here. It ran once, against
# the one database that existed, which is backed up beside it. Schema changes
# from here are Alembic revisions.


async def test_a_message_kept_as_context_is_never_queued(inbox, provider, db):
    """Our own reply is dropped as a trigger but kept as context. If it reaches
    the queue, triage classifies the agent's own output and folds it into the
    task it just acted on — the self-answering loop the scope check exists to
    prevent."""
    provider.emit(make_event(message_id="10", text="api is wrong"))
    provider.emit(
        make_event(
            message_id="20", text="my own reply", is_own=True, mention_type=None
        )
    )
    await captured(inbox)

    assert "my own reply" in [m.text for m in await db.messages()]
    assert [m.text for m in await db.untriaged_mentions()] == ["api is wrong"]


async def test_an_unwatched_mention_type_is_never_queued(provider, db):
    """Watching only direct mentions has to mean it, or configuring the type
    list buys nothing."""
    inbox = Inbox(
        provider=provider,
        db=db,
        config=IngestConfig(
            watched_channels=frozenset({"watched"}),
            mention_types=frozenset({MentionType.DIRECT}),
        ),
    )
    provider.emit(make_event(message_id="10", text="api is wrong"))
    provider.emit(
        make_event(message_id="20", text="hey backend", mention_type=MentionType.ROLE)
    )
    await captured(inbox)

    assert [m.text for m in await db.untriaged_mentions()] == ["api is wrong"]


async def test_context_is_not_reported_as_a_triage_decision(inbox, provider, db):
    """Context is kept, not judged. A row with no decision on it is not one."""
    provider.emit(make_event(message_id="10", text="api is wrong"))
    provider.emit(
        make_event(
            message_id="20", text="my own reply", is_own=True, mention_type=None
        )
    )
    await captured(inbox)

    assert await db.decisions() == []


async def test_two_providers_sharing_a_channel_number_do_not_share_a_history(
    provider, db, config
):
    """Discord and Slack hand out ids from their own namespaces. Nothing stops
    them colliding, and a collision would merge two strangers' conversations."""
    other = FakeProvider()
    other.name = "other"

    provider.emit(make_event(message_id="1", text="from discord"))
    await captured(Inbox(provider=provider, db=db, config=config))
    other.emit(make_event(message_id="2", text="from elsewhere", provider="other"))
    await captured(Inbox(provider=other, db=db, config=config))

    here = ConversationId("fake", "watched")
    there = ConversationId("other", "watched")
    assert [m.text for m in await db.messages(here)] == ["from discord"]
    assert [m.text for m in await db.messages(there)] == ["from elsewhere"]
    assert len(await db.conversations()) == 2


async def test_a_call_made_for_a_task_is_read_back_by_that_task(db):
    """`message_id` is the right key for triage and the wrong one for
    everything downstream.

    An extractor runs on every pass of a task's graph, against many messages;
    a responder answers a task, not a message. So "what did task 42 cost, and
    which prompts produced this reply" was a question the table could not
    answer — and after ticket 01 made those agents record, their rows were
    stored under no key at all.
    """
    task = await db.create_task(
        conversation=ConversationId("fake", "watched"), type="api_issue",
        state=TaskState.PENDING, confidence=0.9, params={},
    )
    common = dict(model="m", system_prompt="s", output="o",
                  input_tokens=1, output_tokens=1)
    await db.record_model_call(
        task_id=task.id, node="prepare", agent="api_issue_extractor",
        prompt="lift the fields out", latency_ms=120, **common,
    )
    await db.record_model_call(
        task_id=task.id, agent="responder", prompt="write it in their voice",
        latency_ms=340, **common,
    )
    await db.record_model_call(agent="summary", prompt="unrelated", **common)

    mine = await db.calls_for_task(task.id)

    assert [c.agent for c in mine] == ["api_issue_extractor", "responder"]
    assert mine[0].node == "prepare"
    assert mine[0].latency_ms == 120
    assert mine[1].node is None
