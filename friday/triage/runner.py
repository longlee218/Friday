"""Applying triage decisions.

Deliberately separate from deciding them. Triage performs no writes, so it is
testable without a database; this is testable without a model. The seam between
them is a decision object.

This also runs off a queue rather than inside the ingest loop. A model call in
that loop would stall the gateway consumer for its duration — the exact failure
the recovery layer exists to prevent, self-inflicted.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, replace

from friday.db import Database
from friday.models import InboundEvent, Task
from friday.triage import Decided, NeedsHuman, TriageOutcome

__all__ = ["PENDING", "NEEDS_HUMAN", "TriageRunner"]

log = logging.getLogger(__name__)

PENDING = "pending"
NEEDS_HUMAN = "needs_human"
# Duplicated in friday.workflows.runner; ticket 05 gives the states one home.
ASKED = "waiting_for_details"


def _record(outcome: TriageOutcome) -> dict:
    """What to keep about a decision, whether or not it opened a task.

    Skips and follow-ups are absorbed elsewhere and would otherwise vanish, and
    they are exactly the decisions a threshold has to be checked against.
    """
    if isinstance(outcome, NeedsHuman):
        return {
            "type": NEEDS_HUMAN,
            "confidence": 0.0,
            "params": {"reason": outcome.reason},
        }
    return {
        "type": outcome.type,
        "confidence": outcome.confidence,
        "params": asdict(outcome.params),
    }


class TriageRunner:
    def __init__(
        self,
        *,
        db: Database,
        triage,
        confidence_threshold: float,
        batch_size: int = 50,
        poll_interval_seconds: float = 2.0,
    ) -> None:
        self._db = db
        self._triage = triage
        self._threshold = confidence_threshold
        self._batch_size = batch_size
        self._poll_interval = poll_interval_seconds

    async def run_forever(self) -> None:
        while True:
            if not await self.run_once():
                await asyncio.sleep(self._poll_interval)

    async def run_once(self) -> list[Task]:
        """Triage everything waiting. Returns the tasks created or updated."""
        touched: list[Task] = []
        for event in await self._db.untriaged_mentions(self._batch_size):
            outcome = await self._decide(event)
            task = await self._apply(event, outcome)
            await self._db.mark_triaged(
                event, task.id if task else None, decision=_record(outcome)
            )
            if task is not None:
                touched.append(task)
        return touched

    async def _decide(self, event: InboundEvent) -> TriageOutcome:
        context = await self._db.messages(event.conversation)
        return await self._triage.decide(event, context=context)

    async def _apply(
        self, event: InboundEvent, outcome: TriageOutcome
    ) -> Task | None:
        if isinstance(outcome, NeedsHuman):
            return await self._open(
                event, "unknown", 0.0, {"reason": outcome.reason}, NEEDS_HUMAN
            )

        if outcome.type == "skip":
            log.debug(
                "skipped %s: %s", event.provider_message_id, outcome.params.reason
            )
            return None

        params = asdict(outcome.params)
        existing = await self._db.open_task_for(event.conversation)
        if existing is not None:
            return await self._follow_up(existing, outcome)

        state = PENDING if outcome.confidence >= self._threshold else NEEDS_HUMAN
        if state == NEEDS_HUMAN:
            log.info(
                "low confidence (%.2f) on %s — asking a human",
                outcome.confidence,
                event.provider_message_id,
            )
        return await self._open(
            event, outcome.type, outcome.confidence, params, state
        )

    async def _follow_up(self, task: Task, outcome: Decided) -> Task:
        """A later message in a conversation already being worked on.

        Two things can arrive in a follow-up. A change of subject — a bug report
        that has turned into something else — must not relabel the task without
        someone noticing. Otherwise it is more detail about the same problem,
        and the answer to a question we asked comes back as an ordinary message:
        if it does not reach the task, the task waits forever for something it
        has already been told.
        """
        if outcome.type != task.type:
            log.info(
                "task %d was %s, follow-up looks like %s — asking a human",
                task.id,
                task.type,
                outcome.type,
            )
            await self._db.set_task_state(task.id, NEEDS_HUMAN)
            return replace(task, state=NEEDS_HUMAN)

        fresh = {k: v for k, v in asdict(outcome.params).items() if v is not None}
        # Only a blank that has just been filled counts as progress. Every
        # follow-up carries a new summary, and reopening on that alone would
        # re-ask the same question for the rest of the conversation.
        gained = [k for k, v in fresh.items() if not task.params.get(k)]
        merged = {**task.params, **fresh}
        if not gained:
            log.debug("task %d: follow-up added no missing detail", task.id)
            if merged == task.params:
                return task
            await self._db.set_task_params(task.id, merged)
            return replace(task, params=merged)

        await self._db.set_task_params(task.id, merged)
        task = replace(task, params=merged)
        log.info("task %d: follow-up supplied %s", task.id, ", ".join(gained))
        if task.state == ASKED:
            await self._db.set_task_state(task.id, PENDING)
            task = replace(task, state=PENDING)
        return task

    async def _open(
        self,
        event: InboundEvent,
        type_: str,
        confidence: float,
        params: dict,
        state: str,
    ) -> Task:
        task = await self._db.create_task(
            conversation=event.conversation,
            type=type_,
            state=state,
            confidence=confidence,
            params=params,
        )
        log.info(
            "task %d opened (%s, %s) from %s",
            task.id,
            type_,
            state,
            event.provider_message_id,
        )
        return task
