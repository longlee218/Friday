"""The outbox repository — a `Database` mixin (ticket 16)."""

from __future__ import annotations

from friday.store._common import *


class OutboxRepo:
    # ---- outbox --------------------------------------------------------

    async def queue_outbound(
        self,
        *,
        task_id: int | None,
        conversation: ConversationId,
        kind: str,
        sender: str,
        text: str,
        reply_to: str | None = None,
        approves: int | None = None,
    ) -> Outbound:
        """`approves` is for an approval card: the row it asks about."""
        row = self._outbound_row(
            task_id=task_id,
            conversation=conversation,
            kind=kind,
            sender=sender,
            text=text,
            reply_to=reply_to,
            approves=approves,
        )
        async with self._sessions.begin() as session:
            session.add(row)
        return _outbound(row)

    @staticmethod
    def _outbound_row(
        *,
        task_id: int | None,
        conversation: ConversationId,
        kind: str,
        sender: str,
        text: str,
        reply_to: str | None = None,
        approves: int | None = None,
    ) -> schema.Outbound:
        """A queued row, not yet added — `queue_outbound`'s, and the spine's
        `deliver_pass`, which adds several in one transaction."""
        # A kind that needs no operator approval is approved by policy, here and
        # now: its payload is frozen at enqueue so the dispatch-time check has
        # something to hold it against, the same hash a reply gets at approval.
        # A reply is left unhashed until the operator releases it.
        by_policy = str(kind) not in _NEEDS_APPROVAL
        row = schema.Outbound(
            task_id=task_id,
            conversation_id=str(conversation),
            kind=str(kind),
            sender=sender,
            text=text,
            reply_to=reply_to,
            approves=approves,
            state=OUTBOUND_QUEUED,
            created_at=_now(),
            approved_by=POLICY if by_policy else None,
            approved_payload_hash=payload_hash(
                kind=str(kind),
                sender=sender,
                conversation=conversation,
                text=text,
                reply_to=reply_to,
            )
            if by_policy
            else None,
        )
        return row

    async def sendable_outbound(self, limit: int = 20) -> list[Outbound]:
        """Queued rows that are allowed out, oldest first.

        The approval check lives here rather than in the sender, so a caller
        cannot forget it: a kind that needs approval is simply not selected
        until the row itself has one. Not its task: an approval on the task
        was written once and never cleared, so every reply queued after the
        first approved one went out unread. `attempts` orders after `id` so a
        row that keeps failing does not monopolise every batch.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Outbound)
                .where(
                    schema.Outbound.state == OUTBOUND_QUEUED,
                    or_(
                        schema.Outbound.retry_after.is_(None),
                        schema.Outbound.retry_after <= _now(),
                    ),
                    or_(
                        schema.Outbound.kind.not_in(_NEEDS_APPROVAL),
                        schema.Outbound.approved_at.is_not(None),
                    ),
                )
                .order_by(schema.Outbound.attempts, schema.Outbound.id)
                .limit(limit)
            )
            return [_outbound(row) for row in rows]

    async def we_sent(self, provider: str, provider_message_id: str, text: str) -> bool:
        """Whether this message is one this agent put there itself.

        Not the same question as "is it from the watched account". The operator
        types from that account too, and a self-mention is how the pipeline is
        tested without a second person. What must never create work is a
        message *this process posted* — answering that is answering itself, and
        it does not stop.

        Matched on the id **or** the text. The id is the precise answer and it
        is not always available in time: the outbox posts, then records the id
        it got back, and the gateway can deliver our own message in between. The
        text is written when the row is queued, long before any of that, so it
        is the half that closes the race. A false positive costs one dropped
        message that repeated our own sentence word for word, from our own
        account.
        """
        async with self._sessions() as session:
            return bool(
                await session.scalar(
                    select(func.count())
                    .select_from(schema.Outbound)
                    .where(
                        (schema.Outbound.sent_message_id == provider_message_id)
                        | (schema.Outbound.text == text)
                    )
                )
            )

    async def posted_by_us(self, provider_message_id: str | None) -> bool:
        """Whether this is a message this agent put there.

        By id alone, unlike `we_sent`. The question here is "did we post the
        thing they replied to", and a reply names a message id — there is no
        text to fall back on and no race to close, because by the time someone
        replies to a message the id has long since been recorded.
        """
        if not provider_message_id:
            return False
        async with self._sessions() as session:
            return bool(
                await session.scalar(
                    select(func.count())
                    .select_from(schema.Outbound)
                    .where(schema.Outbound.sent_message_id == provider_message_id)
                )
            )

    async def mark_outbound_sent(
        self, outbound_id: int, *, sent_message_id: str | None = None
    ) -> None:
        await self._set_outbound(
            outbound_id,
            state=OUTBOUND_SENT,
            sent_at=_now(),
            sent_message_id=sent_message_id,
        )

    async def record_outbound_attempt(
        self, outbound_id: int, error: str, *, retry_after: datetime | None = None
    ) -> None:
        """A failure that will be retried. Back to queued; the count is the bound.

        Back to `queued`, not left `dispatching`: the send was attempted and
        raised — a clean failure, not a crash mid-call — so it is safe to retry
        without the delivery-unknown treatment, and the row must leave the
        `dispatching` marker the outbox wrote before the call or the next pass
        would read the clean failure as an interrupted send.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                update(schema.Outbound)
                .where(schema.Outbound.id == outbound_id)
                .values(
                    state=OUTBOUND_QUEUED,
                    attempts=schema.Outbound.attempts + 1,
                    last_error=scrub(error),
                    retry_after=retry_after,
                )
            )

    async def fail_outbound(self, outbound_id: int, error: str) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(schema.Outbound)
                .where(schema.Outbound.id == outbound_id)
                .values(
                    state=OUTBOUND_FAILED,
                    attempts=schema.Outbound.attempts + 1,
                    # A provider exception can quote an Authorization header,
                    # and this is the only path by which one reaches the store.
                    last_error=scrub(error),
                )
            )

    async def mark_outbound_sent_manually(self, outbound_id: int) -> None:
        """A person delivered it after we gave up. Distinct from `failed`, so
        the trail says it was sent rather than abandoned."""
        await self._set_outbound(
            outbound_id, state=OUTBOUND_SENT_MANUALLY, sent_at=_now()
        )

    async def outbound(
        self, state: str | None = None, *, limit: int | None = None
    ) -> list[Outbound]:
        query = select(schema.Outbound)
        if state is not None:
            query = query.where(schema.Outbound.state == state)
        async with self._sessions() as session:
            rows = await session.scalars(
                query.order_by(schema.Outbound.id).limit(limit)
            )
            return [_outbound(row) for row in rows]

    async def outbound_count(self, task_id: int, *, kind: str) -> int:
        """How many of one kind we have already sent about a task — which is
        what bounds asking the same question."""
        async with self._sessions() as session:
            return await session.scalar(
                select(func.count())
                .select_from(schema.Outbound)
                .where(
                    schema.Outbound.task_id == task_id,
                    schema.Outbound.kind == str(kind),
                )
            )

    async def said_since(self, kind: str, *, since) -> bool:
        """Whether a message of this kind has been queued since `since`.

        For the things the system says about itself, which belong to no task
        and so cannot be counted per task. The outbox row is the record of
        having said something — the same reasoning as `announced` — and it is
        the only record that survives a restart.
        """
        async with self._sessions() as session:
            return bool(
                await session.scalar(
                    select(func.count())
                    .select_from(schema.Outbound)
                    .where(
                        schema.Outbound.kind == str(kind),
                        schema.Outbound.created_at >= since,
                    )
                )
            )

    async def last_outbound_at(self, task_id: int, *, kind: str):
        """When we last said this kind of thing about a task."""
        async with self._sessions() as session:
            return await session.scalar(
                select(func.max(schema.Outbound.created_at)).where(
                    schema.Outbound.task_id == task_id,
                    schema.Outbound.kind == str(kind),
                )
            )

    async def has_newer_message_than(
        self, conversation: ConversationId, message_id: str
    ) -> bool:
        """Has the conversation moved on since that message?

        Numeric on the snowflake, the same way the cursor decides which message
        is newer — as text, '99' would sort after '100'.
        """
        async with self._sessions() as session:
            return (
                await session.scalar(
                    select(func.count())
                    .select_from(schema.Message)
                    .where(
                        schema.Message.conversation_id == str(conversation),
                        cast(schema.Message.provider_message_id, Integer)
                        > cast(literal(message_id), Integer),
                    )
                )
                > 0
            )

    async def _set_outbound(self, outbound_id: int, **values) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(schema.Outbound)
                .where(schema.Outbound.id == outbound_id)
                .values(**values)
            )

    async def cancel_outbound_for(self, task_id: int) -> int:
        """Withdraw everything queued about a task. Returns how many."""
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(schema.Outbound)
                .where(
                    schema.Outbound.task_id == task_id,
                    schema.Outbound.state == OUTBOUND_QUEUED,
                )
                .values(state=OutboundState.CANCELLED)
            )
            return result.rowcount

    async def approve_outbound(self, outbound_id: int, *, by: str) -> None:
        """Record who approved this row and when, and freeze what they approved.

        This is what the outbox selects on, and it releases this row and no
        other. The payload is hashed here, at the moment of approval, so the
        dispatch can tell whether the message still says what the operator saw —
        a text edited afterwards no longer matches and the approval is void.
        """
        row = await self.outbound_row(outbound_id)
        frozen = payload_hash_of(row) if row is not None else None
        await self._set_outbound(
            outbound_id,
            approved_at=_now(),
            approved_by=by,
            approved_payload_hash=frozen,
        )

    async def mark_outbound_dispatching(self, outbound_id: int) -> None:
        """The channel call is about to be made. Written before the send, so a
        crash in between leaves this marker: a row still `dispatching` at
        startup was interrupted, and the outbox reads it as delivery-unknown."""
        await self._set_outbound(outbound_id, state=OUTBOUND_DISPATCHING)

    async def mark_outbound_delivery_unknown(
        self, outbound_id: int, reason: str
    ) -> None:
        """Interrupted mid-send on a channel that cannot dedupe: kept apart from
        `failed` because it may already have gone out, so it waits for the
        operator rather than being retried."""
        await self._set_outbound(
            outbound_id, state=OUTBOUND_DELIVERY_UNKNOWN, last_error=scrub(reason)
        )

    async def outbound_row(self, outbound_id: int) -> Outbound | None:
        """One row by id, or None if there is no such row."""
        async with self._sessions() as session:
            row = await session.get(schema.Outbound, outbound_id)
            return _outbound(row) if row else None
