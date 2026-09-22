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

from friday.domain.models import Outbound
from friday.domain.states import OutboundState, TaskState

NEEDS_HUMAN = TaskState.NEEDS_HUMAN

__all__ = ["FAILED", "Kind", "Outbox", "QUEUED", "record_decision"]

log = logging.getLogger(__name__)


async def record_decision(
    db,
    *,
    outbound_id: int,
    approved: bool,
    by: str,
    by_id: int,
    operator_id: int,
) -> bool:
    """Apply an approval decision to one outbox row, and say whether it stuck.

    A reply goes out *as the operator*, so only the operator may release one.
    That is checked here, against `operator_id`, and not trusted from whatever
    button was pressed: the decision carries the id of whoever the channel
    authenticated (`by_id`), and a decision by anyone else is refused before the
    row can become sendable — a `False` return, nothing approved, no task moved.

    Approving records who and when on the reply row, which is the only thing
    standing between it and the channel — the outbox selects on it. Rejecting
    sends the reply's task back to a human. The row, not the task: a reply the
    task queues later waits for its own card.
    """
    if by_id != operator_id:
        log.warning(
            "refused a decision on reply %d by %s (id %s): only the operator "
            "(id %s) may release a reply that speaks in their name",
            outbound_id, by, by_id, operator_id,
        )
        return False
    if approved:
        await db.approve_outbound(outbound_id, by=by)
        log.info("reply %d approved by %s", outbound_id, by)
        return True
    row = await db.outbound_row(outbound_id)
    if row is None or row.task_id is None:
        log.warning("reply %d rejected by %s, but it has no task", outbound_id, by)
        return True
    await db.move_task(row.task_id, NEEDS_HUMAN)
    log.info("task %d rejected by %s (reply %d)", row.task_id, by, outbound_id)
    return True

#: The two a *reader* asks about — the board, the API and the liveness line all
#: want "what is waiting" and "what gave up". Named here for them; defined in
#: `friday/domain/tasks.py`, which is the only place any of the four is defined.
QUEUED = OutboundState.QUEUED
FAILED = OutboundState.FAILED


#: Which identity a row is sent as. Not an account — a key into the senders
#: the outbox was built with; `run_agent.py` maps it to a provider.
#:
#: Here rather than defaulted twice. The pool has always defaulted to it, and
#: the `api_issue` graph now queues rows of its own before any action reaches
#: the pool — two defaults spelled separately are two answers to one
#: question, and the day they disagree the graph's rows go out as somebody
#: else or not at all.
DEFAULT_SENDER = "discord_user"

#: Which identity reaches the **operator**, privately. Not a stylistic
#: difference from `DEFAULT_SENDER`: that one posts into the conversation as
#: the watched account, so it is public and it is the reporter who reads it;
#: this one direct-messages the operator and ignores the conversation
#: entirely.
#:
#: Getting them the wrong way round is not a cosmetic mistake, and it was
#: made once here (2026-09-22, caught in review): a row meant to tell the
#: operator a cause and the path of the report file was queued as
#: `DEFAULT_SENDER`, which would have posted both into the reporter's channel
#: unapproved — ahead of the approval card, and carrying an absolute path on
#: the operator's own machine. Whoever reads a row's `sender` is deciding who
#: sees it.
DEFAULT_APPROVER = "discord_bot"


class Kind(StrEnum):
    """What a message is, which is what decides whether it needs approval."""

    #: Completing the task's own required parameters — "which environment?".
    #: Not the agent speaking for the operator, so there is nothing to approve.
    ASK_FOR_DETAILS = "ask_for_details"
    #: The request for approval itself. Waiting for approval to send it would
    #: be a deadlock.
    APPROVAL_CARD = "approval_card"
    #: The agent answering in the operator's name. The only kind that waits:
    #: the risk is in answering, not in asking.
    REPLY = "reply"
    #: The system talking about itself: a connection that died, or came back.
    #: Belongs to no task.
    ALERT = "alert"
    #: The once-a-day "still here, this is what I am holding". Its own kind
    #: rather than an `ALERT`, because the two differ in the only way that
    #: matters here: an outage is reported whenever it is true, and this is
    #: reported once. Telling them apart is what lets the row itself answer
    #: "was one sent today?" — and that question has to survive a restart.
    SUMMARY = "summary"
    #: A task nobody can act on. Not a question — the operator is being told,
    #: because a task in a column nobody watches is the same as a lost one.
    HELP_WANTED = "help_wanted"
    #: "I have this and I am working on it", to the reporter, while the work
    #: runs. **Sent without approval, and that is the operator's call**
    #: (2026-09-22): an acknowledgement that waits for a person is an
    #: acknowledgement that arrives after the answer it was meant to precede.
    #: It promises nothing and concludes nothing, which is what makes it
    #: safe to send unread — the risk this queue guards is in *answering*.
    ACKNOWLEDGED = "acknowledged"
    #: An investigation finished: the cause, and where the full report is.
    #: To the operator, not to the reporter, and so not approved — it is the
    #: reading they do *before* approving the reporter's copy, and a card
    #: that waited for its own approval would be a deadlock.
    FINDING = "finding"

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
        if await self._overtaken(row):
            await self._give_up(row, "the conversation moved on before this was approved")
            return
        sender = self._senders.get(row.sender)
        if sender is None:
            await self._give_up(row, f"no sender named {row.sender!r}")
            return
        try:
            sent = await sender.send(row)
        except Exception as exc:  # noqa: BLE001 - every failure is recorded
            await self._retry_or_give_up(row, exc)
            return
        # Marked sent after the call, never before. A crash in between may post
        # twice; the other order loses an approved reply silently, and a lost
        # reply is indistinguishable from the system working.
        await self._db.mark_outbound_sent(row.id, sent_message_id=sent)
        log.info("outbound %d sent as %s: %s", row.id, row.sender, row.text)

    async def _overtaken(self, row: Outbound) -> bool:
        """Has the conversation moved past what this answers?

        Only an *answer* goes stale. A draft is written against a conversation
        that keeps moving and approved minutes or hours later, and answering a
        question since withdrawn, corrected, or answered by someone else is
        worse than saying nothing. Asking for a correlationId is still worth
        asking whatever else has been said.

        Checked here, at the last moment before it goes out, because that is the
        only moment the answer is true or false.
        """
        if row.kind != Kind.REPLY or row.reply_to is None:
            return False
        return await self._db.has_newer_message_than(row.conversation, row.reply_to)

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
        if row.task_id is not None:
            await self._db.move_task(row.task_id, NEEDS_HUMAN)
        log.error("outbound %d gave up: %s", row.id, reason)
