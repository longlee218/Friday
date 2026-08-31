"""Everything the system says to anyone.

A reply is a row, not a call. Deciding what to say and knowing where to put it
are different jobs, and everything that can fail has to be able to fail in one
place — otherwise approval, retry, audit and the manual-send list each grow
their own half-implementation. They are all views over these rows.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from friday.models import Outbound

#: Duplicated from the runners; ticket 05 gives the task states one home.
NEEDS_HUMAN = "needs_human"

__all__ = ["ASKED", "FAILED", "Kind", "Outbox", "QUEUED", "SENT", "SENT_MANUALLY"]

log = logging.getLogger(__name__)

QUEUED = "queued"
SENT = "sent"
FAILED = "failed"
#: Delivered by a person after we gave up. Kept apart from `failed` so the
#: audit trail says "a human sent this" rather than "this was abandoned".
SENT_MANUALLY = "sent_manually"


class Kind(StrEnum):
    """What a message is, which is what decides whether it needs approval."""

    #: Completing the task's own required parameters — "which environment?".
    #: Not the agent speaking for the operator, so there is nothing to approve.
    ASK_FOR_DETAILS = "ask_for_details"
    #: The request for approval itself. Waiting for approval to send it would
    #: be a deadlock.
    APPROVAL_CARD = "approval_card"
    #: The agent answering in the operator's name.
    REPLY = "reply"

    @property
    def needs_approval(self) -> bool:
        return self is Kind.REPLY


class Outbox:
    """The only module that delivers.

    Runs as its own loop. A rate-limited or retrying send inside the loop that
    decides what to do next would stall the deciding, which is the failure the
    recovery layer exists to prevent, self-inflicted.
    """

    def __init__(
        self,
        *,
        db,
        senders: dict,
        max_attempts: int = 3,
        backoff_seconds: float = 30.0,
        batch_size: int = 20,
    ) -> None:
        self._db = db
        self._senders = senders
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._batch_size = batch_size

    async def run_forever(self, poll_interval_seconds: float = 2.0) -> None:
        while True:
            if not await self.run_once():
                await asyncio.sleep(poll_interval_seconds)

    async def run_once(self) -> list[Outbound]:
        """Deliver everything that may go out. Returns what was attempted."""
        rows = await self._db.sendable_outbound(self._batch_size)
        for row in rows:
            await self._deliver(row)
        return rows

    async def _deliver(self, row: Outbound) -> None:
        sender = self._senders.get(row.sender)
        if sender is None:
            await self._give_up(row, f"no sender named {row.sender!r}")
            return
        try:
            await sender.send(row.conversation, row.text, reply_to=row.reply_to)
        except Exception as exc:  # noqa: BLE001 - every failure is recorded
            await self._retry_or_give_up(row, exc)
            return
        # Marked sent after the call, never before. A crash in between may post
        # twice; the other order loses an approved reply silently, and a lost
        # reply is indistinguishable from the system working.
        await self._db.mark_outbound_sent(row.id)
        log.info("outbound %d sent as %s: %s", row.id, row.sender, row.text)

    async def _retry_or_give_up(self, row: Outbound, exc: Exception) -> None:
        if row.attempts + 1 >= self._max_attempts:
            await self._give_up(row, str(exc))
            return
        # Doubling, so a provider that is down is not hammered by a loop that
        # polls every couple of seconds.
        delay = self._backoff * (2**row.attempts)
        await self._db.record_outbound_attempt(
            row.id,
            str(exc),
            retry_after=datetime.now(timezone.utc) + timedelta(seconds=delay)
            if delay
            else None,
        )
        log.warning(
            "outbound %d failed (attempt %d/%d): %s",
            row.id,
            row.attempts + 1,
            self._max_attempts,
            exc,
        )

    async def _give_up(self, row: Outbound, reason: str) -> None:
        """Stop trying, and make sure a person is told.

        The row keeps its text so it can be copied and sent by hand, and the
        task goes back to a human — a message nobody can deliver is work, and
        it has to look like work rather than like a quiet success.
        """
        await self._db.fail_outbound(row.id, reason)
        await self._db.set_task_state(row.task_id, NEEDS_HUMAN)
        log.error("outbound %d gave up, task %d needs a human: %s",
                  row.id, row.task_id, reason)
