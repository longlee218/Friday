"""`FridayState` — what one message's journey carries, the whole way.

Board `every-answer-has-a-shape`, tickets 04 and 06 (D8, D9, D10). It replaces
`MemoryScope`, which named the same room by a second name for a narrower
purpose, and it is what the SDK's per-run `context` means from here on.

A pure value, tested directly — the seam the spec names for this group, and
the one `tests/test_memory_store.py` already drives the store through.
"""

from __future__ import annotations

import dataclasses

import pytest

from friday.domain.models import FridayState


def _state(**over) -> FridayState:
    return dataclasses.replace(
        FridayState(provider="fake", channel_id="c1", agent="triage"), **over
    )


def test_a_field_cannot_be_assigned_from_outside():
    """D9. The whole argument for the named methods below is that nothing
    changes this from a distance; a mutable field would make every one of them
    a convention instead of a rule."""
    state = _state()

    with pytest.raises(dataclasses.FrozenInstanceError):
        state.task_id = 7


def test_the_agent_now_running_is_a_named_change():
    state = _state()

    handed_on = state.as_agent("extractor")

    assert handed_on.agent == "extractor"
    assert state.agent == "triage", "the original was changed underneath its holder"


def test_the_task_a_message_became_is_a_named_change():
    state = _state()

    opened = state.for_task(42)

    assert opened.task_id == 42
    assert state.task_id is None


def test_the_message_a_step_is_about_is_a_named_change():
    state = _state(message_id="m1")

    replying = state.about_message("m2")

    assert replying.message_id == "m2"
    assert state.message_id == "m1"


def test_a_named_change_alters_exactly_one_thing():
    """D9's real content: a method that quietly carried a second change with
    it would put us back where a mutable field was — "what can change, and
    where" stops being a list anybody can read."""
    state = _state(
        thread_id="t1", message_id="m1", author_id="u1", author_name="Nhím",
        reply_to="m0", task_id=1,
    )

    for changed, field in [
        (state.as_agent("responder"), "agent"),
        (state.for_task(2), "task_id"),
        (state.about_message("m9"), "message_id"),
    ]:
        before = dataclasses.asdict(state)
        after = dataclasses.asdict(changed)
        differing = {k for k in before if before[k] != after[k]}
        assert differing == {field}, f"{field} change also moved {differing - {field}}"


def test_a_state_is_built_from_the_conversation_the_work_is_about():
    """The pool has a task, and a task has a provider-qualified conversation.
    Taking it whole is the same argument this class makes one level up: the
    pool used to hand the responder `channel_id`, `task_id` and `message_id`
    as three parameters, having taken the channel off a conversation it was
    already holding."""
    from friday.domain.conversation import ConversationId

    where = ConversationId(provider="fake", channel_id="c1", thread_id="t1")

    state = FridayState.for_conversation(where, agent="responder")

    assert state.provider == "fake"
    assert state.channel_id == "c1"
    assert state.thread_id == "t1"
    assert state.agent == "responder"
    assert state.task_id is None, "a conversation does not know about a task"


def test_a_state_is_built_from_the_message_that_started_the_journey():
    """Seven facts the inbound message already carries, copied once. They used
    to be named again, as a different subset, by every layer that needed any of
    them — so adding one more fact meant threading one more parameter through
    five signatures."""
    from conftest import make_event

    event = make_event(message_id="m1", text="api lỗi rồi")

    state = FridayState.for_event(event, agent="triage")

    assert state.provider == event.provider
    assert state.channel_id == event.channel_id
    assert state.message_id == event.provider_message_id
    assert state.author_id == event.author_id
    assert state.author_name == event.author_name
    assert state.thread_id == event.thread_id
    assert state.reply_to == event.reply_to
    assert state.agent == "triage"
    assert state.task_id is None, "a message has not become a task yet"
