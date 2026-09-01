"""Ticket 26 — only what concerns the operator, and only the parts that matter.

Two problems. Relevance: a busy channel keeps every message once it has ever
mentioned the operator, and unrelated traffic misleads a classification worse
than a few hundred tokens ever cost. Shape: a sliding window changes on every
call, so nothing before it can be cached — an anchored opening plus what has
arrived since is stable at the front and short at the back.
"""

from __future__ import annotations

from conftest import make_event
from friday.domain.conversation import ConversationId

WATCHED = ConversationId("fake", "watched")


def _prompt(event, context) -> str:
    """Stand-in for the old per-module prompt assembler. Tests use it to
    assert structural properties (prefix stability, ordering) of the
    assembled prompt — the bundle is the production path now, but the
    shape this builder produces is what the bundle's conversation section
    renders."""
    lines = []
    if context:
        lines.append("Earlier in this conversation:")
        lines += [f"  {m.author_name}: {m.text}" for m in context]
        lines.append("")
    lines.append("Classify this message:")
    lines.append(f"  {event.author_name}: {event.text}")
    return "\n".join(lines)


async def test_a_message_that_mentions_the_operator_is_relevant(db):
    await db.record_message(make_event(message_id="1", text="hey can you look?"))

    relevant = await db.relevant_messages(WATCHED)

    assert [m.text for m in relevant] == ["hey can you look?"]


async def test_a_message_written_by_the_operator_is_relevant(db):
    await db.record_message(
        make_event(message_id="1", mention_type=None, is_own=True, text="on it")
    )

    relevant = await db.relevant_messages(WATCHED)

    assert [m.text for m in relevant] == ["on it"]


async def test_a_reply_to_the_operator_is_relevant(db):
    await db.record_message(
        make_event(message_id="1", mention_type=None, is_own=True, text="which env?")
    )
    await db.record_message(
        make_event(
            message_id="2", mention_type=None, text="prod", reply_to="1"
        )
    )

    relevant = await db.relevant_messages(WATCHED)

    assert [m.text for m in relevant] == ["which env?", "prod"]


async def test_unrelated_chatter_is_not_relevant(db):
    """Neither addressed to the operator, written by them, nor a reply to
    them — the wall of other people's business a busy channel is full of."""
    await db.record_message(
        make_event(message_id="1", mention_type=None, text="anyone seen the deploy?")
    )

    assert await db.relevant_messages(WATCHED) == []


async def test_a_reply_to_someone_else_is_not_relevant(db):
    await db.record_message(
        make_event(message_id="1", mention_type=None, text="deploy's out")
    )
    await db.record_message(
        make_event(message_id="2", mention_type=None, text="thanks", reply_to="1")
    )

    assert await db.relevant_messages(WATCHED) == []


async def test_changing_the_definition_needs_no_history_that_was_never_stored(db):
    """Filtering happens on read. Everything is kept, whether or not it is
    relevant under today's definition — a message thrown away at write time
    could never be reconsidered under a better one."""
    await db.record_message(
        make_event(message_id="1", mention_type=None, text="unrelated")
    )
    await db.record_message(make_event(message_id="2", text="mentions me"))

    assert {m.text for m in await db.messages(WATCHED)} == {"unrelated", "mentions me"}
    assert [m.text for m in await db.relevant_messages(WATCHED)] == ["mentions me"]


async def test_a_tasks_opening_context_does_not_change_as_the_conversation_continues(db):
    """No limit means nothing is ever evicted — the front of the prompt is
    stable by construction, not by tracking a boundary."""
    await db.record_message(make_event(message_id="1", text="opening question"))
    opening = await db.relevant_messages(WATCHED)

    for n in range(2, 12):
        await db.record_message(
            make_event(message_id=str(n), mention_type=None, text=f"noise {n}")
        )
    await db.record_message(make_event(message_id="20", text="a later mention"))

    later = await db.relevant_messages(WATCHED)

    assert [m.text for m in opening] == ["opening question"]
    assert [m.text for m in later] == ["opening question", "a later mention"]


async def test_later_relevant_messages_are_appended_not_swapped_in(db):
    await db.record_message(make_event(message_id="1", text="first"))
    await db.record_message(make_event(message_id="2", text="second"))

    assert [m.text for m in await db.relevant_messages(WATCHED)] == ["first", "second"]


def _common_prefix_ratio(a: str, b: str) -> float:
    """How much of the shorter string is an identical prefix of the other —
    the fraction of a prompt caching could actually reuse."""
    shorter = min(len(a), len(b))
    matched = 0
    while matched < shorter and a[matched] == b[matched]:
        matched += 1
    return matched / shorter if shorter else 1.0


async def test_the_shared_prefix_between_two_calls_is_almost_the_whole_prompt(db):
    """The proof the shape is stable, not assumed to be: build the same
    conversation's prompt before and after new messages arrive, and check how
    much of it is byte-identical."""
    for n in range(1, 6):
        await db.record_message(make_event(message_id=str(n), text=f"question {n}"))
    early_context = await db.relevant_messages(WATCHED)
    early_prompt = _prompt(make_event(message_id="5", text="question 5"), early_context)

    for n in range(6, 30):
        await db.record_message(
            make_event(message_id=str(n), mention_type=None, text=f"noise {n}")
        )
    new_event = make_event(message_id="30", text="a later mention")
    late_context = await db.relevant_messages(WATCHED)
    late_prompt = _prompt(new_event, late_context)

    ratio = _common_prefix_ratio(early_prompt, late_prompt)

    # A sliding window would share almost nothing here — the 24 lines of noise
    # would have pushed the window's contents apart. The whole opening survives
    # untouched; only the classified-message line at the tail differs.
    assert ratio > 0.9
