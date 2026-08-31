"""Running workflows over tasks that are ready for one."""

from __future__ import annotations

import asyncio
import logging

from friday.db import Database
from friday.tasks import TaskState
from friday.models import Task
from friday.outbox import Kind
from friday.workflows import PARAMS, Action, Ask, Park, plan

__all__ = ["ASKED", "NEEDS_HUMAN", "PENDING", "REVIEW", "WorkflowRunner"]

log = logging.getLogger(__name__)

PENDING = TaskState.PENDING
ASKED = TaskState.WAITING_FOR_DETAILS
NEEDS_HUMAN = TaskState.NEEDS_HUMAN
REVIEW = TaskState.REVIEW


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
        responder=None,
        tone_examples: int = 8,
        max_asks: int = 3,
        sender: str = "discord_user",
        #: Which identity asks. Not the one that speaks: buttons are an
        #: application-only feature, so the question goes out as the bot.
        approver: str = "discord_bot",
        batch_size: int = 20,
    ) -> None:
        self._db = db
        self._auto_ask = auto_ask
        self._responder = responder
        self._tone_examples = tone_examples
        self._max_asks = max_asks
        self._sender = sender
        self._approver = approver
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

        if isinstance(action, Ask):
            asked = await self._db.outbound_count(task.id, kind=Kind.ASK_FOR_DETAILS)
            if asked >= self._max_asks:
                log.info(
                    "task %d: asked %d times without an answer — a human's now",
                    task.id,
                    asked,
                )
                return await self._move(task, NEEDS_HUMAN)

        if isinstance(action, Ask) and self._auto_ask:
            kind, text = await self._say(task, action.text)
            await self._db.queue_outbound(
                task_id=task.id,
                conversation=task.conversation,
                kind=kind,
                sender=self._sender,
                text=text,
                reply_to=await self._db.last_mention_in(task.conversation),
            )
            log.info("task %d: queued a %s — %r", task.id, kind, text)
            if kind is Kind.REPLY:
                # Nobody has been asked yet, and a draft nobody was asked about
                # waits forever. The card is an outbound row like any other, so
                # a card that fails to send shows up rather than going quiet.
                await self._db.queue_outbound(
                    task_id=task.id,
                    conversation=task.conversation,
                    kind=Kind.APPROVAL_CARD,
                    sender=self._approver,
                    text=text,
                )
                # Waiting on the operator, not on the reporter. Different
                # people, different columns, different thing to chase.
                return await self._move(task, REVIEW)
            return await self._move(task, ASKED)

        if isinstance(action, Ask):
            log.info("task %d: would ask, but auto_ask is off", task.id)
        else:
            log.info("task %d: %s", task.id, action.reason)
        return await self._move(task, NEEDS_HUMAN)

    async def _say(self, task: Task, template: str) -> tuple[str, str]:
        """The template, or the same thing in the operator's voice.

        A drafted message is a `reply` and waits for approval. The template is
        allowed out unreviewed because it is the same sentence every time, and
        that stops being true the moment a model writes it.

        A responder that cannot answer falls back to the template rather than
        producing nothing: a wrong reply in someone's name is worse than a
        plain one, and silence is worse than both.
        """
        if self._responder is None:
            return Kind.ASK_FOR_DETAILS, template
        draft = await self._responder.draft(
            asking=template,
            context=await self._db.messages(task.conversation, limit=self._tone_examples),
            tone=await self._db.tone_examples(limit=self._tone_examples),
        )
        if draft is None:
            return Kind.ASK_FOR_DETAILS, template
        return Kind.REPLY, draft.text

    def _plan(self, task: Task) -> Action:
        params = PARAMS.get(task.type)
        if params is None:
            return Park(f"unknown task type {task.type!r}")
        try:
            return plan(task.type, params(**task.params))
        except TypeError as exc:
            # Stored parameters that no longer fit their type — a schema change
            # landing on rows written before it. Work, not a crash.
            return Park(f"cannot read {task.type} parameters: {exc}")

    async def _move(self, task: Task, state: str) -> Task:
        await self._db.move_task(task.id, state)
        from dataclasses import replace

        return replace(task, state=state)
