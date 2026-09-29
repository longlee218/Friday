"""The pass's last step: what the plan came to, as outbox rows and a task
state, in one transaction (build-the-spine ticket 14; board `domains-plug-in`
ticket 14 §2, §8, §10, §11).

```
Ask       a reporter message newer than this pass's Intake → not sent, pass n+1
          asks since the last hand-back ≥ MAX_ASKS_PER_TASK  → HandOver asks_exhausted
          asking switched off                                → the operator, with the question
          else                                               → the question, as written → waiting
Reply     the reply + its approval card                      → review
HandOver  the reason, for `_raise_hands`                     → needs_human
Retriage  a HandOver until re-triage lands (ticket 17)
```

Every text is `scrub`bed before it is queued; an `Ask` goes out as the agent
wrote it — no responder rewrite (§11). One DBOS step, and `deliver_pass`
refuses a pass the task has left, so a re-run queues nothing twice.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from friday.kernel import outbox_card as card
from friday.kernel.domain.states import TaskState
from friday.kernel.ops.redact import scrub
from friday.kernel.outbox import Kind
from friday.sdk.actions import Ask, HandOver, Reply, Retriage

__all__ = ["MAX_ASKS_PER_TASK", "deliver"]

log = logging.getLogger(__name__)

#: Questions one task may put to the reporter between hand-backs; over it the
#: task goes to the operator, `asks_exhausted`. A core constant (§10): a reply
#: with an unchanged placement is not a replan, so `max_replans` does not
#: bound it.
MAX_ASKS_PER_TASK = 3


async def deliver(
    db: Any,
    task: Any,
    pass_no: int,
    outcome: Ask | Reply | HandOver | Retriage,
    *,
    heard_until: datetime | None,
    asks_since: datetime | None,
    sender: str,
    approver: str,
    auto_ask: bool,
) -> str:
    """Route `outcome` for `task` on pass `pass_no`; the state it moved to.
    `heard_until` is the newest reporter turn this pass's Intake read;
    `asks_since` is when the last hand-back was planned (`None`: never)."""
    if isinstance(outcome, Retriage):
        outcome = HandOver(f"retriage: {outcome.reason} — {outcome.found}")
    # A question the reporter may already have answered mid-pass is held,
    # whatever it would have become (`deliver_pass`'s `hold`).
    held = {"hold": isinstance(outcome, Ask), "heard_until": heard_until}
    if isinstance(outcome, Ask):
        if await db.asks_since(task.id, asks_since) >= MAX_ASKS_PER_TASK:
            outcome = HandOver(f"asks_exhausted: {outcome.text}")
        elif not auto_ask:
            log.info("task %d: would ask, but auto_ask is off", task.id)
            return await _move(
                db, task, pass_no, TaskState.NEEDS_HUMAN, pause=outcome.text, **held
            )
        else:
            return await _move(
                db,
                task,
                pass_no,
                TaskState.WAITING_FOR_DETAILS,
                rows=[await _row(db, task, Kind.ASK_FOR_DETAILS, sender, outcome.text)],
                **held,
            )
    if isinstance(outcome, Reply):
        text = outcome.text
        return await _move(
            db,
            task,
            pass_no,
            TaskState.REVIEW,
            rows=[await _row(db, task, Kind.REPLY, sender, text)],
            card=(
                approver,
                lambda reply_id: card.render(
                    text, destination=task.conversation, reply_id=reply_id
                ),
            ),
        )
    return await _move(
        db, task, pass_no, TaskState.NEEDS_HUMAN, pause=outcome.reason, **held
    )


async def _row(db: Any, task: Any, kind: Kind, sender: str, text: str) -> dict:
    return {
        "conversation": task.conversation,
        "kind": kind,
        "sender": sender,
        "text": scrub(text),
        "reply_to": await db.last_mention_in(task.conversation),
    }


async def _move(db: Any, task: Any, pass_no: int, state: TaskState, **rest) -> str:
    moved = await db.deliver_pass(task_id=task.id, pass_no=pass_no, state=state, **rest)
    if moved is None:
        # Delivered already (a crash re-run) or overtaken by the operator.
        log.info("task %d: pass %d already delivered", task.id, pass_no)
        return str((await db.task(task.id)).state)
    if moved != state:
        log.info("task %d: the reporter spoke mid-pass — not asking", task.id)
    log.info("task %d: pass %d → %s", task.id, pass_no, moved)
    return moved
