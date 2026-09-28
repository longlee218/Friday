"""The tasks repository — a `Database` mixin (ticket 16)."""

from __future__ import annotations

from friday.store._common import *  # noqa: F401,F403 (shared store internals)


class TasksRepo:

    # ---- tasks ---------------------------------------------------------

    async def create_task(
        self,
        *,
        conversation: ConversationId,
        type: str,
        state: str,
        confidence: float,
        params: dict,
    ) -> Task:
        row = schema.Task(
            conversation_id=str(conversation),
            type=type,
            state=state,
            confidence=confidence,
            params=params,
            created_at=_now(),
        )
        async with self._sessions.begin() as session:
            session.add(row)
        return _task(row)

    async def task_answered_by(self, provider_message_id: str | None) -> Task | None:
        """The open task a reply is answering, if it is answering one of ours.

        A reply names the message it responds to. When that message is one this
        system sent, the outbound row that produced it already records which
        task it was about — so which task a reply belongs to has an answer that
        is looked up rather than classified.

        `None` when the reply points at something we did not send, at an
        outbound row belonging to no task (a liveness alert, the daily
        summary), or at a task that has since finished. A reply is not a reason
        to reopen work somebody closed.
        """
        if not provider_message_id:
            return None
        async with self._sessions() as session:
            task_id = await session.scalar(
                select(schema.Outbound.task_id).where(
                    schema.Outbound.sent_message_id == provider_message_id
                )
            )
        if task_id is None:
            return None
        found = await self._tasks(
            select(schema.Task).where(
                schema.Task.id == task_id,
                schema.Task.state.in_([str(s) for s in OPEN]),
            )
        )
        return found[0] if found else None

    async def reporter_of(self, task_id: int) -> tuple[str, str] | None:
        """Who opened this task: `(author_id, author_name)` of its first message."""
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(schema.Message.author_id, schema.Message.author_name)
                    .where(schema.Message.task_id == task_id)
                    .order_by(schema.Message.created_at)
                    .limit(1)
                )
            ).first()
        return (row[0], row[1]) if row else None

    async def source_message_of(self, task_id: int) -> str | None:
        """The message that opened this task — its `provider_message_id`.

        The Rooms screen marks this row with the task glyph; the responder
        uses it as the `message_id` on the `FridayState` it hands the
        memory tools, so a memory written while processing this task
        carries the link back to the source message. `None` when the
        task has no message attached (a manually-seeded task, or a
        follow-up where the linkage was lost in a backfill).
        """
        async with self._sessions() as session:
            return await session.scalar(
                select(schema.Message.provider_message_id)
                .where(schema.Message.task_id == task_id)
                .order_by(*_OLDEST_FIRST)
                .limit(1)
            )

    async def last_said_by_reporter(self, task_id: int) -> str | None:
        """The most recent thing the reporter said *into this task's thread* —
        a reply to a message linked to it, or to our own question about it.

        Not their latest message anywhere in the channel. That was the first
        version, and one person filing four reports in one channel produced a
        new announcement for every task each time they typed: the text changed,
        so it was "a new thing to say". What they said about something else is
        not about this. A reply names what it is about; that is the rule used
        for everything else here and it is the rule used here.
        """
        who = await self.reporter_of(task_id)
        if who is None:
            return None
        author_id, _ = who
        linked = select(schema.Message.provider_message_id).where(
            schema.Message.task_id == task_id
        )
        ours_about_it = select(schema.Outbound.sent_message_id).where(
            schema.Outbound.task_id == task_id,
            schema.Outbound.sent_message_id.is_not(None),
        )
        ours_anywhere = select(schema.Outbound.sent_message_id).where(
            schema.Outbound.sent_message_id.is_not(None)
        )
        async with self._sessions() as session:
            latest = await session.scalar(
                select(schema.Message)
                .where(
                    schema.Message.author_id == author_id,
                    # In the channel the operator tests in, the account is
                    # both sides — so "same author" alone would quote our own
                    # question back at them.
                    schema.Message.provider_message_id.not_in(ours_anywhere),
                    or_(
                        schema.Message.reply_to.in_(linked),
                        schema.Message.reply_to.in_(ours_about_it),
                    ),
                )
                .order_by(schema.Message.created_at.desc())
                .limit(1)
            )
        return latest.text if latest is not None else None

    async def unanswered_questions(self, task_id: int) -> tuple[str, ...]:
        """What this task asked the reporter and has not been answered.

        **Derived, never summarised.** No model is involved and none should be:
        this is a query over what was actually sent and what came back, so it
        cannot be wrong in an interesting way. It is the cheapest real context
        on its board and the only one with no token cost at all.

        The failure it exists for is recorded: the system asked for an
        environment, the reporter did not answer, and nothing knew it was
        waiting — so the next pass was free to ask again, and the extractor's
        prompt said nothing about a question already outstanding.

        **Only a sent request for details counts.** A queued one has not been
        asked, so waiting for an answer to it would be waiting for an answer
        to something nobody has seen; a reply or a proposal we sent is not
        something a reporter owes an answer to.

        **Answered means a reply after the asking**, and both halves matter. A
        reply names what it is about, which is the rule `last_said_by_reporter`
        uses and the rule used here; and "after" is what keeps two asks with
        one answer between them from both looking answered. Ordered oldest
        first, so a reader sees them in the order they were asked.
        """
        who = await self.reporter_of(task_id)
        async with self._sessions() as session:
            asks = list(
                await session.scalars(
                    select(schema.Outbound)
                    .where(
                        schema.Outbound.task_id == task_id,
                        schema.Outbound.kind == _ASK,
                        schema.Outbound.sent_at.is_not(None),
                    )
                    .order_by(schema.Outbound.sent_at)
                )
            )
            if not asks or who is None:
                return tuple(a.text for a in asks) if asks else ()
            author_id, _ = who
            ours = select(schema.Outbound.sent_message_id).where(
                schema.Outbound.task_id == task_id,
                schema.Outbound.sent_message_id.is_not(None),
            )
            linked = select(schema.Message.provider_message_id).where(
                schema.Message.task_id == task_id
            )
            answers = list(
                await session.scalars(
                    select(schema.Message.created_at).where(
                        schema.Message.author_id == author_id,
                        or_(
                            schema.Message.reply_to.in_(ours),
                            schema.Message.reply_to.in_(linked),
                        ),
                    )
                )
            )
        return tuple(
            ask.text
            for ask in asks
            if not any(when > ask.sent_at for when in answers)
        )

    async def has_exchanged_with(self, author_id: str) -> bool:
        """Whether the operator and this person have ever replied to each other.

        Not "have both spoken in the same channel" — the operator has spoken
        in every watched channel, which would make everybody known. A reply in
        either direction is an actual exchange, and it is the smallest thing
        that is.
        """
        m, r = schema.Message, aliased(schema.Message)
        async with self._sessions() as session:
            they_replied_to_us = (
                select(func.count())
                .select_from(m)
                .join(r, r.provider_message_id == m.reply_to)
                .where(m.author_id == author_id, r.is_own.is_(True))
            )
            we_replied_to_them = (
                select(func.count())
                .select_from(m)
                .join(r, r.provider_message_id == m.reply_to)
                .where(m.is_own.is_(True), r.author_id == author_id)
            )
            return bool(
                await session.scalar(they_replied_to_us)
                or await session.scalar(we_replied_to_them)
            )

    async def tasks_the_operator_handled(self) -> list[Task]:
        """Open tasks the operator has answered themselves.

        The operator's own messages never create work and are always stored,
        so the record is already here; this reads it. For each open task: has
        the watched account said anything in that conversation since the work
        was **reported** that this process did not post?

        Reported, not opened. `Task.created_at` is `_now()` at the moment
        triage inserted the row, and a message's is Discord's own clock, so
        comparing the two measures the lag between them rather than anything
        about the conversation. That lag is about fourteen seconds on the live
        path — a turn window plus a poll — and hours on a backfill, where the
        task is created now and every message in it was written while the
        process was down. Ticket 46: the operator answering *fast* was the case
        that got missed, and answering quickly is itself what closes the
        reporter's turn, so being quick was what caused the miss.

        Which task a message closes follows the same rule as everything else
        about replies. A reply names what it answers — the reporter's message,
        which is linked to a task, or our own question, whose outbound row is —
        so that task closes. A message that replies to nothing closes the
        conversation's task only when there is exactly one; several open and no
        reply means guessing, and guessing here loses work.

        **That last fallback is what moving the line costs.** A message
        replying to nothing closes the one open task here, and the window it
        is judged in now starts earlier — by the triage lag on the live path,
        and by however long the backfill reached on a cold cursor. So more of
        the operator's own chatter sits inside it, and chatter that answers
        nothing in particular can close a task it was not about. Accepted
        deliberately: the failure on the other side is the agent asking a
        reporter a question the operator already answered, which reaches a
        person, while this one closes a task the operator can reopen.
        """
        handled: list[Task] = []
        for task in await self._tasks(
            select(schema.Task).where(schema.Task.state.in_([str(s) for s in OPEN]))
        ):
            if await self._operator_answered(task):
                handled.append(task)
        return handled

    async def _operator_answered(self, task: Task) -> bool:
        ours = select(schema.Outbound.sent_message_id).where(
            schema.Outbound.sent_message_id.is_not(None)
        )
        async with self._sessions() as session:
            # When the work was reported: the earliest message linked to this
            # task, read off the same clock as the messages compared against
            # it. The same row `source_message_of` returns, by its timestamp
            # rather than its id.
            #
            # A task with nothing linked — manually seeded, or a follow-up
            # whose linkage was lost — has only its own row to go on, and is
            # deliberately left comparing against that.
            since = await session.scalar(
                select(schema.Message.created_at)
                .where(schema.Message.task_id == task.id)
                .order_by(schema.Message.created_at)
                .limit(1)
            )
            if since is None:
                since = task.created_at
            said = (
                await session.execute(
                    select(schema.Message.reply_to)
                    .where(
                        schema.Message.conversation_id == str(task.conversation),
                        schema.Message.is_own.is_(True),
                        schema.Message.created_at > since,
                        schema.Message.provider_message_id.not_in(ours),
                    )
                )
            ).all()
            if not said:
                return False

            for (reply_to,) in said:
                if reply_to is None:
                    continue
                # Did they reply to the reporter, or to our own question?
                via_message = await session.scalar(
                    select(schema.Message.task_id).where(
                        schema.Message.provider_message_id == reply_to
                    )
                )
                via_ours = await session.scalar(
                    select(schema.Outbound.task_id).where(
                        schema.Outbound.sent_message_id == reply_to
                    )
                )
                if task.id in (via_message, via_ours):
                    return True

            # No reply pointing here. Theirs only if it is the one open task
            # in this conversation.
            open_here = await session.scalar(
                select(func.count())
                .select_from(schema.Task)
                .where(
                    schema.Task.conversation_id == str(task.conversation),
                    schema.Task.state.in_([str(s) for s in OPEN]),
                )
            )
            return open_here == 1 and any(r is None for (r,) in said)

    async def open_task_for(self, conversation: ConversationId) -> Task | None:
        """The task this conversation is already working on, if any.

        Open means anything not finished: a follow-up belongs to work in flight,
        whatever stage it has reached.
        """
        async with self._sessions() as session:
            row = await session.scalar(
                select(schema.Task)
                .where(
                    schema.Task.conversation_id == str(conversation),
                    schema.Task.state.in_([str(s) for s in OPEN]),
                )
                .order_by(schema.Task.id.desc())
                .limit(1)
            )
            return _task(row) if row else None

    async def last_mention_in(self, conversation: ConversationId) -> str | None:
        """The most recent message that addressed us here — what a reply to
        this conversation should hang under."""
        async with self._sessions() as session:
            return await session.scalar(
                select(schema.Message.provider_message_id)
                .where(
                    schema.Message.conversation_id == str(conversation),
                    schema.Message.mention_type.is_not(None),
                )
                .order_by(schema.Message.created_at.desc())
                .limit(1)
            )

    async def move_task(self, task_id: int, state: TaskState) -> None:
        """Move a task, refusing anything the graph does not permit.

        Enforced here rather than at each caller: this is the one place every
        move passes through, and a state written by a caller that skipped the
        check is a task nobody polls again.
        """
        async with self._sessions.begin() as session:
            current = await session.scalar(
                select(schema.Task.state).where(schema.Task.id == task_id)
            )
            if current is None:
                raise IllegalTransition(f"no task {task_id}")
            if not may_move(current, state):
                raise IllegalTransition(
                    f"task {task_id} cannot go {current} -> {state}"
                )
            await session.execute(
                update(schema.Task)
                .where(schema.Task.id == task_id)
                .values(state=str(state))
            )

    async def extraction_mark(self, task_id: int) -> ExtractionMark | None:
        """What node 0 last extracted for this task, and from what.

        `None` means nothing has been extracted for it yet: the first pass, or
        a pass whose extraction produced nothing worth remembering.

        **Nothing deletes a mark, and that is deliberate rather than missing.**
        A mark is keyed on a task and task ids are never reused, so a mark for
        a finished task is dead weight bounded by the number of tasks — not the
        "an approved patch outlived the task it belonged to" failure this repo
        has already shipped once, because a mark cannot be acted on: its only
        reader asks for one task by id, and a reopened task's mark is still
        true, since the same text still yields the same answer. If these ever
        need pruning it is the same job as `KEEP_MODEL_CALLS_DAYS`, not a
        cascade.
        """
        async with self._sessions() as session:
            row = await session.get(schema.ExtractionMark, task_id)
            if row is None:
                return None
            return ExtractionMark(
                fingerprint=row.fingerprint,
                params=dict(row.params or {}),
                asked_about=tuple(row.clarify_fields or ()),
                because=row.clarify_because,
            )

    async def mark_extraction(self, task_id: int, mark: ExtractionMark) -> None:
        """Record what the extraction was made from and what it came to.

        Upserted, because there is one current answer per task: a history of
        superseded fingerprints would be a log with no reader.

        The mark carries its own copy of what the extractor produced, which is
        what makes it safe for `set_task_params` to be a separate write: a
        crash between the two leaves a mark whose replay fills the same values
        again, rather than a mark pointing at values nobody stored. An earlier
        version of this docstring claimed the two were one call. They are not.
        """
        values = dict(
            fingerprint=mark.fingerprint,
            params=mark.params,
            clarify_fields=list(mark.asked_about),
            clarify_because=mark.because,
        )
        async with self._sessions.begin() as session:
            existing = await session.get(schema.ExtractionMark, task_id)
            if existing is None:
                session.add(schema.ExtractionMark(task_id=task_id, **values))
                return
            for field_name, value in values.items():
                setattr(existing, field_name, value)

    async def record_ineffective_compaction(self, task_id: int) -> int:
        """One more pass where truncating this task's transcript still left
        it over budget. Returns the new count.

        Upserted the same way `mark_extraction` is — one current count per
        task, created on the first ineffective pass. Never reset: a task
        whose single message is larger than the budget stays that size, so
        there is no future pass on which trying again would help.
        """
        async with self._sessions.begin() as session:
            existing = await session.get(schema.CompactionState, task_id)
            if existing is None:
                session.add(schema.CompactionState(task_id=task_id, ineffective_count=1))
                return 1
            existing.ineffective_count += 1
            return existing.ineffective_count

    async def compaction_on_cooldown(self, task_id: int) -> bool:
        """Whether node 0 should stop attempting budget-based truncation for
        this task — `COMPACTION_COOLDOWN_AFTER` ineffective passes reached.
        `False` for a task nothing has recorded against, which is every task
        before its first ineffective pass."""
        return await self.compaction_ineffective_count(task_id) >= self.COMPACTION_COOLDOWN_AFTER

    async def compaction_ineffective_count(self, task_id: int) -> int:
        """How many passes in a row truncation has failed to help — `0` for a
        task nothing has recorded against. The count `compaction_on_cooldown`
        thresholds; kept as its own read so the operator's own view of a
        stuck task (and this store's own tests) can say *how* stuck, not
        only whether."""
        async with self._sessions() as session:
            row = await session.get(schema.CompactionState, task_id)
            return row.ineffective_count if row is not None else 0

    async def set_task_params(self, task_id: int, params: dict) -> None:
        await self._set_task(task_id, params=params)

    async def _set_task(self, task_id: int, **values) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(schema.Task).where(schema.Task.id == task_id).values(**values)
            )

    async def announced(self, kind: str, *, state: str, limit: int = 20) -> dict:
        """What has already been said about each task in this state.

        Task id -> the set of texts already queued for it. The outbox row is
        the record of having said something, so asking it directly beats
        denormalising the same fact onto the task and keeping the two in step.

        The *text*, not merely the fact of a row. "Told once, ever" is only
        right while the message is the same message: a graph pauses with its
        own question, and when the reporter answers it can pause on a
        different one. A caller that asked only "was anything said?" would see
        the first row and swallow the second question.
        """
        async with self._sessions() as session:
            rows = await session.execute(
                select(schema.Outbound.task_id, schema.Outbound.text)
                .join(schema.Task, schema.Task.id == schema.Outbound.task_id)
                .where(
                    schema.Task.state == str(state),
                    schema.Outbound.kind == str(kind),
                )
                .limit(limit * 8)
            )
            said: dict[int, set[str]] = {}
            for task_id, text in rows:
                said.setdefault(task_id, set()).add(text)
            return said

    async def set_pause(self, task_id: int, question: str) -> None:
        """Record the specific reason a task handed a person the work, so
        `_raise_hands` carries it rather than the task's bare type (ticket 06 —
        the workflow itself suspends now, so this is the pool's note beside it,
        not a checkpoint). Scrubbed: a reason can be a node's exception text."""
        statement = insert(schema.DagState).values(
            task_id=task_id,
            dag_name="",
            results={},
            paused_at_node="",
            paused_question=scrub(question),
            updated_at=_now(),
        )
        async with self._sessions.begin() as session:
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[schema.DagState.task_id],
                    set_={
                        "paused_question": statement.excluded.paused_question,
                        "paused_at_node": statement.excluded.paused_at_node,
                        "updated_at": statement.excluded.updated_at,
                    },
                )
            )

    async def pauses_for(self, task_ids: list[int]) -> dict[int, str]:
        """The reason each of these tasks was handed over, if one was stored.

        In bulk, because `_raise_hands` has a batch and asking per task is how a
        poll that usually finds nothing costs a query each every two seconds.
        """
        if not task_ids:
            return {}
        async with self._sessions() as session:
            rows = await session.execute(
                select(
                    schema.DagState.task_id, schema.DagState.paused_question
                ).where(
                    schema.DagState.task_id.in_(list(task_ids)),
                    schema.DagState.paused_question.is_not(None),
                )
            )
            return {task_id: question for task_id, question in rows}

    async def original_text_for(
        self, task_id: int, limit: int = 20, *, budget_tokens: int | None = None
    ) -> str | None:
        """Everything the reporter has said about this task, oldest first.

        Not the messages *linked* to the task — the ones they wrote. Discord
        lets you send three messages in five seconds, and people do: a mention
        saying the API is broken, then the curl, then which environment. Only
        the first carries a mention, so only the first is in scope, and the
        rest are stored as context with no task on them. The extractor read the
        linked rows and saw one line, and the system asked for a correlationId
        the reporter had sent three seconds earlier.

        So: same conversation, same author as the message that opened the task,
        from that message onwards. Their answer to a question we asked is in
        there too, and so is the second half of their first thought.

        Excluded: anything this system posted. In a self-test the operator is
        both the reporter and the account, so "same author" would otherwise
        include our own questions.

        `limit` bounds the query — a task that stays open in a busy channel
        must not grow its own prompt without limit. Board
        `what-the-room-already-knows`, ticket 08, D6: this stays a *secondary*
        cap now. The primary trigger, when `budget_tokens` is given, is size —
        a count alone cannot tell twenty short messages from twenty long ones.
        Over budget, the oldest of the fetched messages are dropped first,
        never the newest: an answer to a question just asked is the one thing
        a follow-up pass cannot afford to lose, and it is always the newest.
        At least one message always survives the drop, however large — a
        single message this large is what `record_ineffective_compaction`
        exists to make visible, not something this method silently empties.

        `budget_tokens=None` — the default, and unset in `config.yaml` unless
        the operator sets it — means no compaction at all (D7): exactly
        today's behaviour, bounded by `limit` alone.

        **Every verbatim span carries its artifact id, above the span
        itself** (board `read-it-the-way-the-operator-does`, ticket 18):

            [artifact ab12cd34: a curl command]
            curl -X POST …

        The span stays, and that is not a compromise — it is the requirement.
        A correlationId usually arrives *inside* the response the reporter
        pasted (D2), so an extractor shown only a reference could not lift
        one out; a first attempt at this ticket hid the content and two tests
        said so immediately.

        What the id buys is that nothing has to be **retyped**. Task 6, on
        2026-09-20, carried 678 characters of Bearer token in its message and
        stored 676 in `params.curl`: one character gone out of a base64
        segment, because everything on the path from here to a parameter went
        through a model and the model copied it out by hand. The stored
        request then fails a signature nobody broke. So a field that is a
        whole verbatim span — `curl` — asks for the id, and
        `friday.kernel.dag.prepare.resolve_artifacts` puts the content back; a field
        that is a *value inside* one — `correlation_id` — is still read from
        the text, which is a copy short enough to be right.
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
                return None
            conversation_id, author_id, opened_at = opening

            ours = select(schema.Outbound.sent_message_id).where(
                schema.Outbound.sent_message_id.is_not(None)
            )
            said = await session.execute(
                select(
                    schema.Message.redacted_text, schema.Message.original_text
                )
                .where(
                    schema.Message.conversation_id == conversation_id,
                    schema.Message.author_id == author_id,
                    schema.Message.created_at >= opened_at,
                    schema.Message.provider_message_id.not_in(ours),
                )
                .order_by(schema.Message.created_at)
                .limit(limit)
            )
            rows = list(said)
            held = await self._artifact_contents(
                session, conversation_id, author_id, opened_at
            )
            texts = [
                # Scrubbed here as well as at the artifact, because a
                # reporter who pastes a request without a code fence produces
                # no artifact at all, and this is the read that becomes the
                # prompt (finding C: "`scrub` covers logs and the board, not
                # the prompt sent to the provider").
                scrub(_with_artifact_ids(redacted, original, held))
                for redacted, original in rows
                if (redacted or original)
            ]
        if not texts:
            return None
        if budget_tokens is not None:
            while len(texts) > 1 and estimated_tokens("\n".join(texts)) > budget_tokens:
                texts = texts[1:]
        return "\n".join(texts) or None

    # ---- workflow graph state -------------------------------------------

    async def tasks_in_state(self, state: str, limit: int = 20) -> list[Task]:
        return await self._tasks(
            select(schema.Task)
            .where(schema.Task.state == state)
            .order_by(schema.Task.id)
            .limit(limit)
        )

    async def tasks(self, *, limit: int | None = None) -> list[Task]:
        return await self._tasks(
            select(schema.Task).order_by(schema.Task.id).limit(limit)
        )

    async def task(self, task_id: int) -> Task | None:
        """One task, freshest read — the pool's deps factory rebuilds a run's
        `Deps` from the current row, not a snapshot handed to an earlier pass."""
        async with self._sessions() as session:
            row = await session.get(schema.Task, task_id)
            return _task(row) if row is not None else None

    async def _tasks(self, query) -> list[Task]:
        async with self._sessions() as session:
            return [_task(row) for row in await session.scalars(query)]
