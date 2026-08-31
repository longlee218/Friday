"""Running workflows over tasks that are ready for one."""

from __future__ import annotations

import asyncio
import logging

from friday.db import Database
from friday.models import Task
from friday.outbox import Kind
from friday.triage import ApiIssueParams
from friday.workflows import Ask, Park, plan_api_issue

__all__ = ["ASKED", "NEEDS_HUMAN", "PENDING", "WorkflowRunner"]

log = logging.getLogger(__name__)

PENDING = "pending"
ASKED = "waiting_for_details"
NEEDS_HUMAN = "needs_human"


class WorkflowRunner:
    """Turns a pending task into an action.

    Produces outbound intents; it never delivers one. Deciding what to say and
    knowing where to put it are different jobs, and the Outbox is the only
    module that talks to a provider.

    Only one message is allowed out without review: the request for missing
    details. It completes the task's own required parameters rather than
    speaking for the operator, and being wrong about it costs the reporter one
    unnecessary question.
    """

    def __init__(
        self,
        *,
        db: Database,
        auto_ask: bool,
        sender: str = "discord_user",
        batch_size: int = 20,
    ) -> None:
        self._db = db
        self._auto_ask = auto_ask
        self._sender = sender
        self._batch_size = batch_size

    async def run_forever(self, poll_interval_seconds: float = 2.0) -> None:
        while True:
            if not await self.run_once():
                await asyncio.sleep(poll_interval_seconds)

    async def run_once(self) -> list[Task]:
        acted: list[Task] = []
        for task in await self._db.tasks_in_state(PENDING, self._batch_size):
            acted.append(await self._act(task))
        return acted

    async def _act(self, task: Task) -> Task:
        action = self._plan(task)

        if isinstance(action, Ask) and self._auto_ask:
            await self._db.queue_outbound(
                task_id=task.id,
                conversation=task.conversation,
                kind=Kind.ASK_FOR_DETAILS,
                sender=self._sender,
                text=action.text,
                reply_to=await self._db.last_mention_in(task.conversation),
            )
            log.info("task %d: queued a request for missing details", task.id)
            return await self._move(task, ASKED)

        if isinstance(action, Ask):
            log.info("task %d: would ask, but auto_ask is off", task.id)
        else:
            log.info("task %d: %s", task.id, action.reason)
        return await self._move(task, NEEDS_HUMAN)

    def _plan(self, task: Task):
        if task.type != "api_issue":
            return Park(f"no workflow for {task.type} yet")
        return plan_api_issue(ApiIssueParams(**task.params))

    async def _move(self, task: Task, state: str) -> Task:
        await self._db.set_task_state(task.id, state)
        from dataclasses import replace

        return replace(task, state=state)
