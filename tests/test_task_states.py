"""Ticket 05 — the state graph, in one place.

The names were written down in four modules and the legal moves between them
nowhere. A typo was an unreachable task, and "never drop a mention" is the one
invariant this system has.
"""

from __future__ import annotations

import pytest

from friday.domain.conversation import ConversationId
from friday.domain.tasks import IllegalTransition, TaskState, may_move

WATCHED = ConversationId("fake", "watched")


async def task(db, state=TaskState.PENDING):
    return await db.create_task(
        conversation=WATCHED, type="api_issue", state=state,
        confidence=0.9, params={"summary": "s"},
    )


def test_the_states_are_named_once():
    assert TaskState.PENDING == "pending"
    assert TaskState.WAITING_FOR_DETAILS == "waiting_for_details"
    assert TaskState.NEEDS_HUMAN == "needs_human"
    assert TaskState.DONE == "done"


@pytest.mark.parametrize(
    "current, target",
    [
        (TaskState.PENDING, TaskState.WAITING_FOR_DETAILS),
        (TaskState.WAITING_FOR_DETAILS, TaskState.PENDING),
        (TaskState.PENDING, TaskState.NEEDS_HUMAN),
        (TaskState.NEEDS_HUMAN, TaskState.DONE),
        (TaskState.NEEDS_HUMAN, TaskState.PENDING),
    ],
)
def test_the_moves_the_system_actually_makes_are_legal(current, target):
    assert may_move(current, target)


def test_a_finished_task_stays_finished():
    """Otherwise a stray follow-up reopens work someone closed."""
    assert not may_move(TaskState.DONE, TaskState.PENDING)


async def test_an_illegal_move_is_refused_rather_than_written(db):
    finished = await task(db, TaskState.DONE)

    with pytest.raises(IllegalTransition):
        await db.move_task(finished.id, TaskState.PENDING)

    assert (await db.tasks())[0].state == TaskState.DONE


async def test_a_legal_move_is_applied(db):
    opened = await task(db)

    await db.move_task(opened.id, TaskState.WAITING_FOR_DETAILS)

    assert (await db.tasks())[0].state == TaskState.WAITING_FOR_DETAILS


async def test_moving_to_where_it_already_is_is_not_an_error(db):
    """Two deliveries of the same follow-up must not crash the runner."""
    opened = await task(db)

    await db.move_task(opened.id, TaskState.PENDING)

    assert (await db.tasks())[0].state == TaskState.PENDING
