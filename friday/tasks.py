"""What states a task can be in, and which moves between them are legal.

These names were written down in four modules and the legal moves between them
in none. A typo was a task in a state nobody polls — invisible, and
indistinguishable from correct operation. That is exactly the failure the whole
design is built to avoid for mentions, and a task is a mention that got as far
as being understood.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ALLOWED", "IllegalTransition", "TaskState", "may_move"]


class IllegalTransition(Exception):
    """A move the state graph does not permit. Raised rather than written,
    because the alternative is a task that quietly stops being worked on."""


class TaskState(StrEnum):
    #: Ready for a workflow to act on.
    PENDING = "pending"
    #: We asked the reporter for something and are waiting for it.
    WAITING_FOR_DETAILS = "waiting_for_details"
    #: A person has to look. Every failure lands here — low confidence, a
    #: changed subject, a refused send, a type nothing can act on yet.
    NEEDS_HUMAN = "needs_human"
    #: A draft is waiting for approval before it can be sent. Ticket 06 fills
    #: this in; it is named here so the graph is complete rather than growing
    #: a state at the moment it is first needed.
    REVIEW = "review"
    #: Finished. Terminal, so a stray follow-up cannot reopen work someone
    #: deliberately closed.
    DONE = "done"


ALLOWED: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset(
        {TaskState.WAITING_FOR_DETAILS, TaskState.NEEDS_HUMAN, TaskState.REVIEW,
         TaskState.DONE}
    ),
    # Back to pending when an answer arrives, or when one is still missing and
    # the workflow has to ask again.
    TaskState.WAITING_FOR_DETAILS: frozenset(
        {TaskState.PENDING, TaskState.NEEDS_HUMAN, TaskState.DONE}
    ),
    # A person can hand it back to the machine, or close it.
    TaskState.NEEDS_HUMAN: frozenset({TaskState.PENDING, TaskState.DONE}),
    TaskState.REVIEW: frozenset(
        {TaskState.PENDING, TaskState.NEEDS_HUMAN, TaskState.DONE}
    ),
    TaskState.DONE: frozenset(),
}

#: States a task is still being worked in. `open_task_for` uses this, so adding
#: a state does not silently make follow-ups open a second task.
OPEN = frozenset(ALLOWED) - {TaskState.DONE}


def may_move(current: TaskState | str, target: TaskState | str) -> bool:
    """Staying put is always allowed: two deliveries of the same follow-up
    must not crash a runner."""
    current, target = TaskState(current), TaskState(target)
    return target is current or target in ALLOWED[current]
