"""Ticket 16, first slice — whose voice is it.

`is_own` means "sent by the watched account", which is true of the operator's
own messages *and* of everything the agent sent as them. For every other
purpose those are the same thing. For learning a voice they are opposites: an
agent trained on its own output amplifies it every round.
"""

from __future__ import annotations

from conftest import captured, make_event

from friday.kernel.domain.conversation import ConversationId
from friday.kernel.outbox import Kind

WATCHED = ConversationId("fake", "watched")


async def test_the_operators_own_messages_are_offered_as_examples(inbox, provider, db):
    provider.emit(make_event(message_id="10", text="someone asks"))
    provider.emit(make_event(message_id="20", text="on it, give me a sec", is_own=True))
    await captured(inbox)

    assert [m.text for m in await db.tone_examples(limit=5)] == ["on it, give me a sec"]


async def test_what_the_agent_sent_is_not_offered_as_an_example(inbox, provider, db):
    """It arrives back over the gateway as one of ours, because it was sent
    under the same account. Learning from it is learning from ourselves."""
    task = await db.create_task(
        conversation=WATCHED,
        type="backend.trace_problem",
        state="pending",
        confidence=0.9,
        params={},
    )
    row = await db.queue_outbound(
        task_id=task.id,
        conversation=WATCHED,
        kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user",
        text="Could you send which environment?",
    )
    await db.mark_outbound_sent(row.id, sent_message_id="30")

    provider.emit(make_event(message_id="1", text="someone asks"))
    provider.emit(
        make_event(
            message_id="30", text="Could you send which environment?", is_own=True
        )
    )
    provider.emit(make_event(message_id="40", text="my own words", is_own=True))
    await captured(inbox)

    assert [m.text for m in await db.tone_examples(limit=5)] == ["my own words"]


async def test_examples_are_the_most_recent_ones(inbox, provider, db):
    """Voice drifts. The last few messages describe it better than the first."""
    provider.emit(make_event(message_id="x", text="someone asks"))
    for n in range(6):
        provider.emit(make_event(message_id=str(n), text=f"reply {n}", is_own=True))
    await captured(inbox)

    assert [m.text for m in await db.tone_examples(limit=3)] == [
        "reply 3",
        "reply 4",
        "reply 5",
    ]


async def test_our_words_are_recognised_even_without_an_id(inbox, provider, db):
    """`Provider.send` is not required to return an id, and rows sent before it
    did have none. Matching the text is the fallback: the operator echoing the
    agent's own sentence back is not their voice either."""
    task = await db.create_task(
        conversation=WATCHED,
        type="backend.trace_problem",
        state="pending",
        confidence=0.9,
        params={},
    )
    row = await db.queue_outbound(
        task_id=task.id,
        conversation=WATCHED,
        kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user",
        text="Could you send which environment?",
    )
    await db.mark_outbound_sent(row.id)  # no id came back

    provider.emit(make_event(message_id="1", text="someone asks"))
    provider.emit(
        make_event(
            message_id="30", text="Could you send which environment?", is_own=True
        )
    )
    provider.emit(make_event(message_id="40", text="my own words", is_own=True))
    await captured(inbox)

    assert [m.text for m in await db.tone_examples(limit=5)] == ["my own words"]
