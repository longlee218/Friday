"""The messages repository — a `Database` mixin (ticket 16)."""

from __future__ import annotations

from friday.store._common import *


class MessagesRepo:
    # ---- messages ------------------------------------------------------

    async def record_message(
        self, event: InboundEvent, *, context_only: bool = False
    ) -> bool:
        """Store a message. False if this one was already recorded.

        The primary key is (provider, provider_message_id), which is what makes
        the two delivery paths safe to run concurrently.

        `context_only` keeps a message without ever classifying it. Such a
        message may well carry a mention type — our own replies in a DM always
        do — so the mention type alone cannot decide what belongs in the queue.
        Two kinds arrive this way: history seeded from before a conversation
        involved us, and messages the inbox ruled out of scope but kept because
        a conversation missing half of itself does not read.
        """
        statement = insert(schema.Message).values(
            provider=event.provider,
            provider_message_id=event.provider_message_id,
            channel_id=event.channel_id,
            thread_id=event.thread_id,
            conversation_id=str(event.conversation),
            author_id=event.author_id,
            author_name=event.author_name,
            text=event.text,
            original_text=event.text,
            created_at=event.created_at,
            is_own=event.is_own,
            mention_type=event.mention_type.value if event.mention_type else None,
            reply_to=event.reply_to,
            triaged_at=_now() if context_only else None,
        )
        async with self._sessions.begin() as session:
            result = await session.execute(statement.on_conflict_do_nothing())
            inserted = result.rowcount == 1
        # Split *after* the insert lands, and only on the insert that
        # actually happened: `record_message` is called from two delivery
        # paths on the same key (CLAUDE.md's dedup rule), and creating an
        # artifact on a conflicting, already-recorded call would write it
        # twice for one message.
        if inserted and event.code:
            await self._record_artifacts(event)
        return inserted

    async def _record_artifacts(self, event: InboundEvent) -> None:
        """Split verbatim material out of a newly recorded message into its
        own artifacts, and store a redacted rendering of its text alongside
        it — the summariser's own read, and the reader `record_message`
        never re-runs this on (D8's "the reader of an artifact may not
        itself produce one": this runs once, from the message that produced
        `event.code`, never from an artifact's own `content`).

        `event.text` already carries this message's code back in place —
        `friday.kernel.providers.discord.normalise` calls `transform` once and
        restores it — so redacting it here re-splits already-restored text
        rather than the original raw message. That re-split agrees with the
        first one for every case this ticket's own tests exercise, but it is
        not a proof: content whose own body contains a literal triple
        backtick can make `transform`'s non-greedy fence match end sooner
        the second time than the first, changing the span count `redact`
        finds. Rather than let that surface as an uncaught `ValueError` with
        the message row already committed — which is the failure this
        method degrades away from, not one it can rule out by construction
        — a mismatch here is caught, logged, and left as if the message had
        carried no code at all: no artifacts, `redacted_text` stays `NULL`,
        every reader falls back to `text`. The one reader that matters,
        `relevant_messages_in_channel`, then shows this one message's code
        to the summariser exactly as it would have before this ticket —
        which is a known, narrow gap, not silent corruption of a different
        message's redaction.
        """
        now = _now()
        ids_and_descriptions = [
            (_artifact_id(), _describe_artifact(body)) for body in event.code
        ]
        refs = [f"[artifact {aid}: {desc}]" for aid, desc in ids_and_descriptions]
        try:
            redacted_text = redact(event.text, refs)
        except ValueError:
            log.warning(
                "%s/%s: code split differently on re-read — no artifact "
                "recorded, the summariser will see this message's raw text",
                event.provider,
                event.provider_message_id,
            )
            return
        rows = [
            schema.Artifact(
                id=artifact_id,
                channel_id=event.channel_id,
                provider=event.provider,
                source_message_id=event.provider_message_id,
                # Finding C: the reporter's curl carries their own Bearer
                # token, and `sensitive_words` deliberately does not match it
                # — the prefilter decides whether a message may reach a
                # third-party API at all, and a curl must reach it. So
                # nothing stopped the credential being written down. `scrub`
                # already knew the pattern; this is a place it runs.
                #
                # Here rather than further downstream because this row is
                # what every later reader copies from: the parameter, the
                # extractor's prompt, the outbox row that quotes the request,
                # the report file. Scrubbed once, at the write, is the only
                # version of this that cannot be forgotten at a call site.
                # The request itself survives whole — only the credential
                # goes, and the header's name stays, because *that* one was
                # sent is evidence a diagnosis often turns on.
                content=scrub(body),
                description=description,
                # Microseconds apart, not all at `now`: several spans from
                # one message would otherwise tie on `created_at`, and
                # `artifacts_for_message`'s ordering — the same order
                # `event.code` already lists them in — would depend on
                # SQLite breaking the tie by insertion order, which nothing
                # here asks for or checks.
                created_at=now + timedelta(microseconds=i),
            )
            for i, (body, (artifact_id, description)) in enumerate(
                zip(event.code, ids_and_descriptions)
            )
        ]
        # Between this write and `record_message`'s own — never atomic with
        # it, since the artifacts do not exist until this message's insert
        # is known to have landed (see the comment at the call site) — a
        # process crash leaves `redacted_text` `NULL` for this one message,
        # the same fallback as the `ValueError` case above. Accepted for the
        # same reason: narrow, self-limiting to one message, and the
        # alternative (one transaction spanning both) would mean generating
        # artifact ids before knowing the insert will not conflict.
        async with self._sessions.begin() as session:
            session.add_all(rows)
            await session.execute(
                update(schema.Message)
                .where(
                    schema.Message.provider == event.provider,
                    schema.Message.provider_message_id == event.provider_message_id,
                )
                .values(redacted_text=redacted_text)
            )

    async def artifacts_for_message(
        self, provider: str, provider_message_id: str
    ) -> list[Artifact]:
        """Every artifact one message produced, in the order they were
        written — which is `event.code`'s own order, since that is the only
        thing `_record_artifacts` ever iterates."""
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Artifact)
                .where(
                    schema.Artifact.provider == provider,
                    schema.Artifact.source_message_id == provider_message_id,
                )
                .order_by(schema.Artifact.created_at)
            )
            return [_artifact(row) for row in rows]

    async def _artifact_contents(
        self, session, conversation_id: str, author_id: str, opened_at
    ) -> dict[str, str]:
        """Artifact id -> content, for one reporter's messages in one
        conversation. The query `original_text_for` and `artifacts_for_task`
        share, so what a transcript references and what a parameter may name
        cannot drift apart."""
        theirs = select(schema.Message.provider_message_id).where(
            schema.Message.conversation_id == conversation_id,
            schema.Message.author_id == author_id,
            schema.Message.created_at >= opened_at,
        )
        rows = await session.execute(
            select(schema.Artifact.id, schema.Artifact.content)
            .where(schema.Artifact.source_message_id.in_(theirs))
            .order_by(schema.Artifact.created_at)
        )
        return {artifact_id: content for artifact_id, content in rows}

    async def artifacts_for_task(self, task_id: int) -> dict[str, str]:
        """Every verbatim span this task's reporter sent, by artifact id.

        The same reach as `original_text_for` — same conversation, same
        author, from the opening message onwards — because these are the
        references that appear in what the extractor was shown, and a
        parameter may only name one of those. An id from another room is an
        id this returns nothing for, which is what makes naming one useless.
        """
        async with self._sessions() as session:
            opening = (
                await session.execute(
                    select(
                        schema.Message.conversation_id,
                        schema.Message.author_id,
                        schema.Message.created_at,
                    )
                    .where(schema.Message.task_id == task_id)
                    .order_by(schema.Message.created_at)
                    .limit(1)
                )
            ).first()
            if opening is None:
                return {}
            conversation_id, author_id, opened_at = opening

            return await self._artifact_contents(
                session, conversation_id, author_id, opened_at
            )

    async def messages(
        self, conversation: ConversationId | None = None, *, limit: int | None = None
    ) -> list[InboundEvent]:
        """Messages in chronological order, most recent `limit` of them.

        Bounded because this is what a model is given as context: unbounded, the
        prompt grows with the channel and a long-running conversation eventually
        costs more than it explains.
        """
        query = select(schema.Message)
        if conversation is not None:
            query = query.where(schema.Message.conversation_id == str(conversation))
        if limit is None:
            return await self._events(query.order_by(*_OLDEST_FIRST))
        newest = await self._events(query.order_by(*_NEWEST_FIRST).limit(limit))
        return list(reversed(newest))

    async def relevant_messages(
        self, conversation: ConversationId
    ) -> list[InboundEvent]:
        """What concerns the operator in this conversation: mentions them, was
        written by them, or replies to something they wrote. Chronological,
        unbounded.

        This is context assembled *for a call*, not what gets stored — a
        message thrown away at write time can never be reconsidered under a
        better definition of relevant later, so nothing here is deleted, only
        read selectively. Unbounded rather than the last N: a sliding window
        changes on every call, so nothing before it can ever be cached; the
        structural filter already keeps a busy channel's unrelated traffic out,
        so a message once relevant stays part of the prefix forever and only
        the tail grows as the conversation continues.
        """
        return await self._events(
            self._relevant(
                schema.Message.conversation_id == str(conversation)
            ).order_by(*_OLDEST_FIRST)
        )

    async def relevant_messages_in_channel(
        self, provider: str, channel_id: str
    ) -> list[InboundEvent]:
        """The same filter, scoped to a whole channel rather than one
        conversation.

        A thread is its own conversation (see `friday.conversation`) — reading
        by `conversation_id` alone would miss every reply happening inside one.
        A channel-level summary needs everything under the channel, threads
        included, which is what `channel_id` — a separate column every message
        under it shares — gives directly.

        **The only caller that reads `redacted_text` in preference to `text`**
        (board `what-the-room-already-knows`, ticket 07, D8) — this feeds the
        summariser, the one build that must never see an artifact's content.
        A message with nothing split out of it, or recorded before this
        column existed, has `redacted_text is None`; falling back to `text`
        there is not a special case, it is what an unaffected row already is.
        """
        scope = (
            schema.Message.provider == provider,
            schema.Message.channel_id == channel_id,
        )
        events = await self._events(self._relevant(*scope).order_by(*_OLDEST_FIRST))
        async with self._sessions() as session:
            redacted = dict(
                (
                    await session.execute(
                        select(
                            schema.Message.provider_message_id,
                            schema.Message.redacted_text,
                        ).where(*scope)
                    )
                ).all()
            )
        return [
            replace(e, text=redacted.get(e.provider_message_id) or e.text)
            for e in events
        ]

    def _relevant(self, *scope):
        own = select(schema.Message.provider_message_id).where(
            *scope, schema.Message.is_own.is_(True)
        )
        return select(schema.Message).where(
            *scope,
            or_(
                schema.Message.mention_type.is_not(None),
                schema.Message.is_own.is_(True),
                schema.Message.reply_to.in_(own),
            ),
        )

    async def page_messages(
        self, *, limit: int = 50, before: str | None = None
    ) -> list[InboundEvent]:
        """A page of the feed, newest first.

        Keyset rather than offset: the table is written to constantly, and an
        offset would skip or repeat rows as it grows underneath the reader.
        """
        query = select(schema.Message)
        if before is not None:
            query = query.where(
                cast(schema.Message.provider_message_id, Integer)
                < cast(literal(before), Integer)
            )
        return await self._events(query.order_by(*_NEWEST_FIRST).limit(limit))

    async def mentions(self) -> list[InboundEvent]:
        """Only the messages that addressed us."""
        return await self._events(
            select(schema.Message)
            .where(schema.Message.mention_type.is_not(None))
            .order_by(schema.Message.created_at, schema.Message.provider_message_id)
        )

    async def turn_from(self, event: InboundEvent) -> tuple[list[InboundEvent], bool]:
        """The turn this message opens: everything the same person said in the
        same conversation from here on, up to the first message by somebody
        else. Returns the messages and whether somebody else has since spoken.

        Worked out when read, not stored. At the moment a message arrives it is
        not known whether the turn is over — the next message is three seconds
        away and has not happened — so a stored turn id would be wrong for as
        long as the turn is still running.

        What this process posted is left out: in a channel the operator tests
        in, the account is both sides of the conversation.
        """
        ours = select(schema.Outbound.sent_message_id).where(
            schema.Outbound.sent_message_id.is_not(None)
        )
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Message)
                .where(
                    schema.Message.conversation_id == str(event.conversation),
                    schema.Message.created_at >= event.created_at,
                    schema.Message.provider_message_id.not_in(ours),
                )
                .order_by(schema.Message.created_at)
            )
            turn: list[InboundEvent] = []
            for row in rows:
                if row.author_id != event.author_id:
                    return turn, True
                turn.append(_event(row))
            return turn, False

    async def untriaged_mentions(self, limit: int = 50) -> list[InboundEvent]:
        """The queue: work nobody has looked at, oldest first.

        Context shares the table but is never classified — the queue is a
        `WHERE` clause, not a second table. `context_only` stamps `triaged_at`
        on the way in, so that one column carries the whole answer.

        It used to say `mention_type IS NOT NULL` as well, which was the same
        question asked a second way and by a worse proxy. The two agreed until
        a reply to one of our own messages became work: it addresses us and
        mentions nobody, so the inbox let it in and this threw it away. The
        agent asked a question, the reporter answered, and the answer sat in
        the table having been accepted and never queued.

        The inbox decides what is work. This reads that decision; it does not
        take it again.
        """
        return await self._events(
            select(schema.Message)
            .where(schema.Message.triaged_at.is_(None))
            .order_by(schema.Message.created_at)
            .limit(limit)
        )

    async def mark_triaged(
        self,
        event: InboundEvent,
        task_id: int | None = None,
        *,
        decision: dict | None = None,
    ) -> None:
        """Close the queue entry, and keep what triage concluded about it."""
        decision = decision or {}
        async with self._sessions.begin() as session:
            await session.execute(
                update(schema.Message)
                .where(
                    schema.Message.provider == event.provider,
                    schema.Message.provider_message_id == event.provider_message_id,
                )
                .values(
                    triaged_at=_now(),
                    task_id=task_id,
                    decision_type=decision.get("type"),
                    decision_confidence=decision.get("confidence"),
                    decision_params=decision.get("params", {}),
                )
            )

    async def _events(self, query) -> list[InboundEvent]:
        """Run a `messages` query and return `InboundEvent`s with the two
        room-marker fields populated (`task_id` from the row itself,
        `is_enrichment` from a left join against `memories.source_message_id`).

        One query for both. A right join would lose messages with no
        memory; a left join with the right `IS NOT NULL` filter in the
        `WHERE` is what makes "messages, plus a yes/no on memory" a
        single round trip.
        """
        enriched = query.add_columns(
            schema.Memory.source_message_id.is_not(None).label("is_enrichment")
        ).outerjoin(
            schema.Memory,
            schema.Memory.source_message_id == schema.Message.provider_message_id,
        )
        async with self._sessions() as session:
            rows = await session.execute(enriched)
            return [
                replace(_event(row[0]), is_enrichment=row.is_enrichment) for row in rows
            ]

    async def tone_examples(self, limit: int = 8) -> list[InboundEvent]:
        """The operator's own recent messages, for a model to learn a voice from.

        Excludes anything the agent sent: it goes out under the same account
        and comes back over the gateway indistinguishable from a real one, so
        without this the responder learns its own voice and amplifies it every
        round. Real examples carry a tone that a written style guide does not,
        which is the whole reason for reading them.
        """
        sent = (
            select(schema.Outbound)
            .where(schema.Outbound.state.in_((OUTBOUND_SENT, OUTBOUND_SENT_MANUALLY)))
            .subquery()
        )
        our_ids = select(sent.c.sent_message_id).where(
            sent.c.sent_message_id.is_not(None)
        )
        # Matching the text too, because an id is not guaranteed: `send` may
        # return none, and rows sent before it did have none at all. An
        # operator echoing back the agent's own sentence is not their voice
        # either, so a false positive here costs nothing.
        our_words = select(sent.c.text)
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Message)
                .where(
                    schema.Message.is_own.is_(True),
                    schema.Message.provider_message_id.not_in(our_ids),
                    schema.Message.text.not_in(our_words),
                )
                # Tie-broken on the id, cast as a number: two messages can
                # share a timestamp, and a snowflake is the platform's own
                # answer to which came first. Same reason `advance_cursor`
                # casts rather than comparing text.
                .order_by(
                    schema.Message.created_at.desc(),
                    cast(schema.Message.provider_message_id, Integer).desc(),
                )
                .limit(limit)
            )
            return list(reversed([_event(row) for row in rows]))
