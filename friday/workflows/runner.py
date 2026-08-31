"""Running workflows over tasks that are ready for one."""

from __future__ import annotations

import asyncio
import logging

from friday.db import Database
from friday.models import Task
from friday.triage import ApiIssueParams
from friday.workflows import Ask, Park, plan_api_issue

__all__ = ["ASKED", "NEEDS_HUMAN", "PENDING", "WorkflowRunner"]

log = logging.getLogger(__name__)

PENDING = "pending"
ASKED = "waiting_for_details"
NEEDS_HUMAN = "needs_human"


class WorkflowRunner:
    """Turns a pending task into an action.

    Only one reply is allowed out without review: the request for missing
    details. It is the same question every time, and if the classification was
    wrong it costs someone a single unnecessary question. Everything else parks
    for a human until the approval path exists.
    """

    def __init__(
        self, *, db: Database, provider, auto_ask: bool, batch_size: int = 20
    ) -> None:
        self._db = db
        self._provider = provider
        self._auto_ask = auto_ask
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
            await self._provider.send(task.conversation_id, action.text)
            log.info(
                "task %d: replied to %s: %s",
                task.id,
                task.conversation_id,
                action.text,
            )
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
