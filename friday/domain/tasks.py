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
    #: The operator answered it themselves. Not `done`: a person did the work
    #: rather than the agent, it has to be reopenable because closing on "they
    #: said something in this channel" will sometimes be wrong, and the share
    #: of tasks that end here is the one number that says whether this system
    #: is helping.
    HANDLED_BY_OPERATOR = "handled_by_operator"


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
    TaskState.HANDLED_BY_OPERATOR: frozenset({TaskState.PENDING}),
}
for _state in (TaskState.PENDING, TaskState.WAITING_FOR_DETAILS, TaskState.NEEDS_HUMAN, TaskState.REVIEW):
    ALLOWED[_state] = ALLOWED[_state] | {TaskState.HANDLED_BY_OPERATOR}

#: States a task is still being worked in. `open_task_for` uses this, so adding
#: a state does not silently make follow-ups open a second task.
OPEN = frozenset(ALLOWED) - {TaskState.DONE, TaskState.HANDLED_BY_OPERATOR}


def may_move(current: TaskState | str, target: TaskState | str) -> bool:
    """Staying put is always allowed: two deliveries of the same follow-up
    must not crash a runner."""
    current, target = TaskState(current), TaskState(target)
    return target is current or target in ALLOWED[current]


class OutboundState(StrEnum):
    """Where an outbound row is in its life.

    Here rather than in `friday/outbox/` or `friday/store/db.py`, because it
    was in both: the outbox held `QUEUED/SENT/FAILED/SENT_MANUALLY` for its
    readers and the store held `OUTBOUND_*` for its `WHERE` clauses, four
    strings written twice. Two of the outbox's four had no reader left, which
    is what a duplicated vocabulary looks like as it rots — one copy stops
    being used and nothing says so.
    """

    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    #: Delivered by a person after we gave up. Kept apart from `failed` so the
    #: audit trail says "a human sent this" rather than "this was abandoned".
    SENT_MANUALLY = "sent_manually"
    #: Withdrawn before it went out, because the operator answered first. Kept
    #: rather than deleted so the board can show what was about to be said.
    CANCELLED = "cancelled"
