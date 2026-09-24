"""Everything the system says to anyone.

A reply is a row, not a call. Deciding what to say and knowing where to put it
are different jobs, and everything that can fail has to be able to fail in one
place — otherwise approval, retry, audit and the manual-send list each grow
their own half-implementation. They are all views over these rows.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from friday.domain.models import POLICY, Outbound, payload_hash, payload_hash_of
from friday.domain.states import OutboundState, TaskState
from friday.sdk.outbox import Kind

NEEDS_HUMAN = TaskState.NEEDS_HUMAN
DISPATCHING = OutboundState.DISPATCHING
DELIVERY_UNKNOWN = OutboundState.DELIVERY_UNKNOWN

__all__ = [
    "FAILED",
    "Kind",
    "Outbox",
    "POLICY",
    "QUEUED",
    "payload_hash",
    "record_decision",
]

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
        durable: Callable[[int, int], Awaitable[object]] | None = None,
    ) -> None:
        self._db = db
        self._senders = senders
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._batch_size = batch_size
        # How one delivery is made durable: `(outbound_id, attempt) -> awaitable`
        # that runs `deliver_once` inside a DBOS workflow keyed to the row and
        # attempt, so a crash mid-send resumes exactly once (ticket 07). `None`
        # delivers directly in-process — the same code path, no durability — so
        # the loop is testable without an engine and the composition root is the
        # one place that wires DBOS in.
        self._durable = durable

    async def run_forever(self, poll_interval_seconds: float = 2.0) -> None:
        while True:
            if not await self.run_once():
                await asyncio.sleep(poll_interval_seconds)

    async def run_once(self) -> list[Outbound]:
        """Deliver everything that may go out. Returns what was attempted."""
        rows = await self._db.sendable_outbound(self._batch_size)
        for row in rows:
            if self._durable is not None:
                await self._durable(row.id, row.attempts)
            else:
                await self.deliver_once(row.id)
        return rows

    async def deliver_once(self, outbound_id: int) -> str:
        """One delivery attempt, crash-safe, told by its outcome.

        The durable unit: the DBOS step runs exactly this, and so does the
        in-process path. Written so re-running it after a crash is safe — the
        double-post the whole ticket exists to prevent.

        A row found already `dispatching` was interrupted mid-send: the marker
        was written and the channel called, but the process died before the
        result was recorded. On a channel that can dedupe (it takes an
        idempotency key) re-sending is safe; on one that cannot, the outcome is
        genuinely unknown, so the row waits for the operator rather than being
        retried into a possible double-post.
        """
        row = await self._db.outbound_row(outbound_id)
        if row is None:
            return "gone"
        # Only a row still on the way out is delivered. A resumed step whose row
        # another pass already finished with — sent, failed, delivery_unknown,
        # cancelled — reads it here and stops, so re-running is a no-op rather
        # than a second send.
        if row.state not in (QUEUED, DISPATCHING):
            return row.state

        interrupted = row.state == DISPATCHING
        sender = self._senders.get(row.sender)
        if sender is None:
            await self._give_up(row, f"no sender named {row.sender!r}")
            return "no_sender"

        if interrupted and not _idempotent(sender):
            await self._delivery_unknown(row)
            return "delivery_unknown"

        # Both guards still run before a (re-)send: an answer can go stale and an
        # approved message can be edited while the row waits, and neither is
        # something a resumed send should ignore.
        if await self._overtaken(row):
            await self._give_up(row, "the conversation moved on before this was approved")
            return "overtaken"
        if not self._payload_matches(row):
            await self._give_up(
                row, "the message changed after it was approved, so the approval no longer applies"
            )
            return "stale_approval"

        # Written before the call, so a crash in between leaves the marker that
        # `dispatching` at startup is read as. The other order (mark sent first)
        # loses an approved reply silently, and a lost reply is indistinguishable
        # from the system working.
        await self._db.mark_outbound_dispatching(row.id)
        try:
            sent = await self._send(sender, row)
        except Exception as exc:  # noqa: BLE001 - every failure is recorded
            await self._retry_or_give_up(row, exc)
            return "retry"
        await self._db.mark_outbound_sent(row.id, sent_message_id=sent)
        log.info("outbound %d sent as %s: %s", row.id, row.sender, row.text)
        return "sent"

    async def _send(self, sender, row: Outbound):
        """Hand the row to its channel, with an idempotency key when the channel
        can use one. The key is the row itself: stable across every retry, so a
        channel that dedupes sees the same send twice as one."""
        if _idempotent(sender):
            return await sender.send(row, idempotency_key=f"outbox-{row.id}")
        return await sender.send(row)

    def _payload_matches(self, row: Outbound) -> bool:
        """Whether the row still says what was approved. `None` never matches —
        a sendable row without a frozen hash is a bug upstream, not something to
        release unchecked."""
        if row.approved_payload_hash is None:
            return False
        return row.approved_payload_hash == payload_hash_of(row)

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

    async def _delivery_unknown(self, row: Outbound) -> None:
        """A send we cannot account for, handed to the operator.

        Kept apart from `failed`: `failed` means it never went out and can be
        sent by hand, but this one *may* already have gone out, so re-sending it
        might double-post. Only the operator can look and decide, so the row
        stops here and its task goes to a human — never an automatic retry.
        """
        await self._db.mark_outbound_delivery_unknown(
            row.id, "interrupted mid-send; the outcome is unknown"
        )
        if row.task_id is not None:
            await self._db.move_task(row.task_id, NEEDS_HUMAN)
        log.error("outbound %d delivery unknown: interrupted mid-send", row.id)


def _idempotent(sender: object) -> bool:
    """Whether a channel can dedupe a repeated send from an idempotency key. A
    sender opts in with `supports_idempotency = True`; the default is `False`,
    the safe assumption for a channel we have not proven dedupes."""
    return bool(getattr(sender, "supports_idempotency", False))
