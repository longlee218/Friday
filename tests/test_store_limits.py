"""Ticket 17, first slice — the store answers bounded questions.

Everything here loaded whole tables and sliced in Python. That is invisible at
twenty rows and fatal at twenty thousand, and the one place it was already
wrong — model calls — showed the oldest rows rather than the newest.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import captured, make_event
from friday.kernel.domain.conversation import ConversationId
from friday.kernel.outbox import Kind
from friday.kernel.domain.states import TaskState

WATCHED = ConversationId("fake", "watched")


async def test_model_calls_come_back_newest_first(db):
    """It sorted ascending and then limited, so past the limit it returned the
    oldest calls — the ones nobody asks about."""
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for n in range(5):
        await db.record_model_call(
            message_id=str(n), agent="triage", model="m", system_prompt="s",
            prompt="p", output="o", input_tokens=1, output_tokens=1,
            created_at=start + timedelta(hours=n),
        )

    assert [c.message_id for c in await db.model_calls(limit=2)] == ["4", "3"]


async def test_a_conversations_context_is_the_most_recent_of_it(inbox, provider, db):
    """Unbounded before this: a long conversation would have been handed to the
    model in full, and the prompt grows with the channel."""
    provider.emit(make_event(message_id="1", text="first"))
    await captured(inbox)
    for n in range(2, 7):
        provider.emit(make_event(message_id=str(n), text=f"message {n}"))
    await captured(inbox)

    texts = [m.text for m in await db.messages(WATCHED, limit=3)]

    assert texts == ["message 4", "message 5", "message 6"]


async def test_the_feed_pages_backwards_from_the_newest(inbox, provider, db):
    for n in range(1, 6):
        provider.emit(make_event(message_id=str(n), text=f"message {n}"))
    await captured(inbox)

    first = await db.page_messages(limit=2)
    assert [m.text for m in first] == ["message 5", "message 4"]

    second = await db.page_messages(limit=2, before=first[-1].provider_message_id)
    assert [m.text for m in second] == ["message 3", "message 2"]


async def test_counts_do_not_require_loading_the_rows(inbox, provider, db):
    """The heartbeat ran every 60 seconds and loaded the whole message table to
    print one number."""
    provider.emit(make_event(message_id="1"))
    await captured(inbox)
    await db.create_task(
        conversation=WATCHED, type="devops.api_issue", state=TaskState.PENDING,
        confidence=0.9, params={},
    )

    counts = await db.counts()

    assert counts["messages"] == 1
    assert counts["untriaged"] == 1
    assert counts["tasks"] == {"pending": 1}
    assert counts["outbound"] == {}


async def test_a_provider_error_is_scrubbed_before_it_is_stored(db):
    """The one path by which a provider exception reaches the database, and
    `friday/redact.py` was written claiming to cover it."""
    task = await db.create_task(
        conversation=WATCHED, type="devops.api_issue", state=TaskState.PENDING,
        confidence=0.9, params={},
    )
    row = await db.queue_outbound(
        task_id=task.id, conversation=WATCHED, kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user", text="which environment?",
    )

    await db.fail_outbound(row.id, "401 for Bearer sk-abcdefghijklmnopqrstuvwxyz01")

    (stored,) = await db.outbound()
    assert "sk-abcdefghijklmnopqrstuvwxyz01" not in stored.last_error
    assert "REDACTED" in stored.last_error
