"""The only store.

A deep module: every caller sees domain dataclasses, and nothing above this
seam knows SQLAlchemy exists. Mapped classes live in `friday.schema` and are
converted at the edge, so `friday.models` stays free of persistence concerns.

Never call this from a sync path — a blocking database call on the event loop
stalls ingestion.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from fnmatch import fnmatch
from typing import Any
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    Integer,
    cast,
    delete,
    event,
    func,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased
from sqlalchemy.pool import StaticPool

from friday.agent.structured import fits
from friday.store import schema
from friday.domain.conversation import ConversationId
from friday.domain.memory_guard import InstructionShaped, check_not_instruction_shaped
from friday.domain.states import OutboundState
from friday.domain.models import (
    Artifact,
    CandidateStatus,
    DOMAIN_KINDS,
    ExtractionMark,
    Memory,
    MemoryCandidate,
    MemoryKind,
    DECISIONS,
    FridayState,
    MEMORY_DATA,
    MemoryKeyTaken,
    MemoryOrigin,
    MemoryRefused,
    MemoryStatus,
    InboundEvent,
    MentionType,
    MessageFlow,
    ModelCall,
    MonitorEvent,
    MonitorSnapshot,
    RunningTask,
    ToolCall,
    Outbound,
    Task,
    natural_key,
    writers_for,
)
from friday.text.transform import redact
from friday.ops.redact import scrub
from friday.domain.states import OPEN, IllegalTransition, TaskState, may_move

__all__ = ["Database", "estimated_tokens"]

log = logging.getLogger(__name__)



def estimated_tokens(text: str) -> int:
    """Characters divided by four (D5) — an estimate, and the name says so.

    The configured provider is MiniMax, for which there is no tokenizer; a
    tokenizer for a different vendor would be confidently wrong rather than
    roughly right. Exported so `friday/dag/prepare.py` measures a budget's
    outcome with the exact same arithmetic this module used to enforce it —
    one formula, not two that could drift.
    """
    return len(text) // 4

_OLDEST_FIRST = (schema.Message.created_at, schema.Message.provider_message_id)
#: Ties break on the id cast as a number — two messages can share a timestamp,
#: and a snowflake is the platform's own answer to which came first.
_NEWEST_FIRST = (
    schema.Message.created_at.desc(),
    cast(schema.Message.provider_message_id, Integer).desc(),
)

#: Aliases, not definitions — `friday/domain/tasks.py` owns these. Kept as
#: local names because they read better in a `WHERE` clause than the enum
#: does, and renamed away from the enum's members so nothing here can quietly
#: become a second source.
OUTBOUND_QUEUED = OutboundState.QUEUED
OUTBOUND_SENT = OutboundState.SENT
OUTBOUND_FAILED = OutboundState.FAILED
OUTBOUND_SENT_MANUALLY = OutboundState.SENT_MANUALLY

#: Kinds the outbox refuses to select without an approval on the row. Kept as
#: data here because it is a `WHERE` clause; `friday.outbox.Kind` is where the
#: reasoning lives.
_NEEDS_APPROVAL = ("reply",)

#: The one kind that is a question to a reporter. Data here for the same
#: reason `_NEEDS_APPROVAL` is: it is a `WHERE` clause, and importing
#: `friday.outbox.Kind` would put the store below a module that reads it.
_ASK = "ask_for_details"


def _engine(path: str):
    """One engine per process. In-memory needs `StaticPool`: without it every
    checkout opens a *different* empty database."""
    if path == ":memory:":
        return create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _wal(connection, _record):
        connection.execute("PRAGMA journal_mode=WAL")

    return engine


class Database:
    def __init__(self, engine, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._engine = engine
        self._sessions = sessions
        #: Held across `memory_add`'s count and insert, which are two awaits
        #: apart — see there.
        self._memory_slots = asyncio.Lock()

    @classmethod
    async def connect(cls, path: str, *, create: bool = False) -> "Database":
        """Open the store. `create` builds the schema straight from the models.

        Off by default, because a real database gets its shape from Alembic and
        `run_agent.migrate()` has already run by the time this is called.
        Building tables here as well would hide a forgotten revision: every
        fresh database would work, and only the one that already exists would
        break. Tests opt in; nothing else should.
        """
        engine = _engine(path)
        if create:
            async with engine.begin() as connection:
                await connection.run_sync(schema.Base.metadata.create_all)
        return cls(engine, async_sessionmaker(engine, expire_on_commit=False))

    async def close(self) -> None:
        await self._engine.dispose()

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
        `friday.providers.discord.normalise` calls `transform` once and
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
                event.provider, event.provider_message_id,
            )
            return
        rows = [
            schema.Artifact(
                id=artifact_id,
                channel_id=event.channel_id,
                provider=event.provider,
                source_message_id=event.provider_message_id,
                content=body,
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
                    schema.Message.provider_message_id
                    == event.provider_message_id,
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
            self._relevant(schema.Message.conversation_id == str(conversation))
            .order_by(*_OLDEST_FIRST)
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

    async def decisions(self) -> list[dict]:
        """Every triage decision, oldest first. The evidence for the threshold."""
        async with self._sessions() as session:
            rows = await session.scalars(
                # A decision, not a closed queue entry: context is stored with
                # a `triaged_at` so it never queues, but nothing judged it.
                select(schema.Message)
                .where(schema.Message.decision_type.is_not(None))
                .order_by(schema.Message.triaged_at)
            )
            return [
                {
                    "provider": row.provider,
                    "message_id": row.provider_message_id,
                    "type": row.decision_type,
                    "confidence": row.decision_confidence,
                    "params": row.decision_params or {},
                    "task_id": row.task_id,
                    "triaged_at": row.triaged_at,
                }
                for row in rows
            ]

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
                replace(_event(row[0]), is_enrichment=row.is_enrichment)
                for row in rows
            ]

    async def rooms(self) -> list[dict]:
        """Every conversation this system has seen, for the left-hand list.

        One query set rather than one request per room: a sidebar that had to
        fetch each room's messages to say how many there are is a sidebar
        that gets slower the more work you have.

        `name` is the operator's own label and `None` when they have not
        given one — which is not the same as `""`, and is why the writer
        turns an empty string back into `None` rather than storing a name
        that renders as nothing.

        **Driven from `messages`, with the name joined on.** It joined
        `conversations` first, which only `record_conversation` writes — so a
        room whose row had never been written disappeared from this list
        while its messages sat plainly in the table. That is the same shape
        as a dropped mention: a room that is not listed is indistinguishable
        from a room that never existed. What makes a room real here is that
        somebody said something in it; the name is decoration on top.
        """
        counted = (
            select(
                schema.Message.conversation_id,
                func.count().label("messages"),
                func.max(schema.Message.created_at).label("last_at"),
                func.min(schema.Message.channel_id).label("channel_id"),
            )
            .group_by(schema.Message.conversation_id)
            .subquery()
        )
        query = (
            select(
                counted.c.conversation_id,
                schema.Conversation.name,
                counted.c.messages,
                counted.c.last_at,
                counted.c.channel_id,
            )
            .outerjoin(
                schema.Conversation,
                schema.Conversation.id == counted.c.conversation_id,
            )
            .order_by(counted.c.last_at.desc())
        )
        async with self._sessions() as session:
            return [
                {
                    "id": row.conversation_id,
                    "name": row.name,
                    "channel_id": row.channel_id,
                    "messages": row.messages,
                    "last_at": row.last_at,
                }
                for row in await session.execute(query)
            ]

    async def name_conversation(self, conversation_id: str, name: str) -> bool:
        """Give a room a name, or take its name back. `False` if no such room.

        An empty name is stored as `NULL`, not as `""`: "unnamed" is a state
        the list already renders, and a second spelling of it would mean two
        ways to be nameless and a bug the day one of them is missed.

        Creates the row when the room has messages but no `conversations`
        entry — the same gap `rooms` was hiding. A room the operator can see
        and cannot label would be a worse answer than either.
        """
        async with self._sessions.begin() as session:
            spoke = await session.scalar(
                select(func.count())
                .select_from(schema.Message)
                .where(schema.Message.conversation_id == conversation_id)
            )
            if not spoke:
                return False
            row = await session.get(schema.Conversation, conversation_id)
            if row is None:
                row = schema.Conversation(id=conversation_id)
                session.add(row)
            row.name = name.strip() or None
            return True

    async def flow_for(
        self, *, provider: str, message_id: str
    ) -> MessageFlow | None:
        """Everything that followed from one message, in one request.

        **"One request", not "one instant", and the difference is deliberate.**
        D6 argues against joining in the browser because "four requests read four
        instants of a database being written to". This narrows that window to
        microseconds inside one process — the reads are `_calls_about` (one
        session, both tables), the outbound rows, the message and its task, and
        the turn — but SQLite in WAL gives each session its own snapshot, so it
        is not one atomic read and this docstring said it was.

        Left as several sessions on purpose. Making it atomic means threading a
        session through `turn_from` and the outbound reader, which are shared
        with callers that have no such need, and what is bought is a torn *debug
        view* rather than a wrong decision — nothing acts on this. The claim is
        corrected instead, which is the half that was actually wrong.

        One method rather than four calls a browser joins (D6): a task can
        change state between the second request and the third, and the path
        rendered would be one that never existed. `/api/board` is one
        aggregate for the same reason and says so.

        `None` only when there is no such message. A message nothing has
        triaged yet comes back with `decision=None`, which is a state — queued
        and unread — and not an absence.

        The two correlation keys are read separately and merged, because they
        are separate facts: triage's call names a message and no task, since
        no task existed when it ran; everything after names the task and not
        the message. Merging here is the join this exists to do — a caller
        handed two lists is a caller doing it again, differently.
        """
        async with self._sessions() as session:
            row = await session.get(schema.Message, (provider, message_id))
            if row is None:
                return None
            event = _event(row)
            task = await session.get(schema.Task, row.task_id) if row.task_id else None

        calls, tools = await self._calls_about(message_id, row.task_id)
        outbound = (
            await self._outbound_for_task(row.task_id)
            if row.task_id is not None
            else []
        )

        turn, _ = await self.turn_from(event)
        return MessageFlow(
            message=event,
            # `turn_from` shows only what it can attribute to the reporter, so
            # a message this process posted has an empty turn. Falling back to
            # the message itself keeps the path readable rather than showing a
            # flow whose first step is missing.
            turn=turn or [event],
            decision=(
                {
                    "type": row.decision_type,
                    "confidence": row.decision_confidence,
                    "params": row.decision_params or {},
                }
                if row.triaged_at is not None
                else None
            ),
            triaged_at=row.triaged_at,
            task=_task(task) if task is not None else None,
            # Already ordered by `_calls_about`, in SQL, with the row id as
            # the tiebreak — which is better than the sort that used to be
            # here: neither dataclass carries the id, so a Python sort had
            # only the timestamp and leaned on the merge order for ties.
            model_calls=calls,
            tool_calls=tools,
            outbound=outbound,
        )

    async def _calls_about(
        self, message_id: str, task_id: int | None
    ) -> tuple[list[ModelCall], list[ToolCall]]:
        """Every model and tool call that names this message *or* its task.

        `OR` in one query rather than two lists concatenated, and that is a
        correctness fix rather than a tidiness one. The first version read the
        two keys separately and appended, on the assumption that no call
        carries both — and nothing enforces that: `_About` in
        `friday/agent/harness.py` holds `message_id` and `task_id`
        independently, and `Harness.run`'s docstring invites both ("a caller
        supplies whichever it knows"). Only triage passes one today, so the
        assumption held by coincidence of the current call sites. The first
        node to pass both would have had every call rendered twice and its
        tokens counted twice on the task screen.

        Ordered here rather than by the caller, since the two kinds are one
        sequence: triage's call names the message and everything after names
        the task.

        **Known limitation: `message_id` is not scoped by provider.** Neither
        `model_calls` nor `tool_calls` carries a provider column — only the
        message table does, as half of its composite key — so a call is
        correlated by a bare id. `flow_for` takes a provider and uses it for
        the message lookup alone, which is honest today because there is one
        provider and Discord snowflakes do not collide with themselves. It
        stops being honest the day a second provider exists: two messages
        could share an id and each would show the other's calls. Recorded
        rather than fixed because the fix is a column and a migration, and
        the trigger is a change nobody has made.

        Recorded as a **tripwire**, not as this paragraph:
        `test_there_is_still_only_one_provider_name` fails the day a second
        provider name appears and says what it means for this method. A note
        naming a trigger condition is worth what the next person reading it
        is worth, and this repo's own convention is that the rules only
        written down are the ones that drifted.
        """
        by_key = lambda table: (  # noqa: E731
            (table.message_id == message_id) | (table.task_id == task_id)
            if task_id is not None
            else (table.message_id == message_id)
        )
        async with self._sessions() as session:
            calls = await session.scalars(
                select(schema.ModelCall)
                .where(by_key(schema.ModelCall))
                .order_by(schema.ModelCall.created_at.asc(), schema.ModelCall.id.asc())
            )
            model_calls = [_model_call(row) for row in calls]
            tools = await session.scalars(
                select(schema.ToolCall)
                .where(by_key(schema.ToolCall))
                .order_by(schema.ToolCall.created_at.asc(), schema.ToolCall.id.asc())
            )
            return model_calls, [_tool_call(row) for row in tools]

    async def _outbound_for_task(self, task_id: int) -> list[Outbound]:
        """Everything queued about one task, oldest first — the end of a path."""
        query = (
            select(schema.Outbound)
            .where(schema.Outbound.task_id == task_id)
            .order_by(schema.Outbound.id)
        )
        async with self._sessions() as session:
            return [_outbound(row) for row in await session.scalars(query)]

    # ---- model calls ---------------------------------------------------

    async def record_model_call(self, **values) -> None:
        values.setdefault("created_at", _now())
        async with self._sessions.begin() as session:
            session.add(schema.ModelCall(**values))
        # Publish on the bus so the SSE stream sees the event the
        # moment the row is written. The payload carries the row
        # id and the agent the screen renders; the SSE endpoint
        # serialises it as JSON. The store does not wait for the
        # bus — `publish` is sync and never blocks — so a slow
        # subscriber cannot stall a model call.
        from friday.ops.events import get_bus
        task_id = values.get("task_id")
        message_id: str | None = None
        if task_id is not None:
            src = await self.source_message_of_task(int(task_id))
            if src is not None:
                provider, mid = src
                message_id = f"{provider}:{mid}"
        get_bus().publish(
            type_="model_call",
            payload={
                "row_id": values.get("id"),
                "agent": values.get("agent"),
                "task_id": task_id,
                "latency_ms": values.get("latency_ms"),
                "attempt": values.get("attempt") or 1,
                "message_id": message_id,
            },
        )

    async def record_tool_call(self, **values) -> None:
        values.setdefault("created_at", _now())
        async with self._sessions.begin() as session:
            session.add(schema.ToolCall(**values))
        from friday.ops.events import get_bus
        task_id = values.get("task_id")
        message_id: str | None = None
        if task_id is not None:
            src = await self.source_message_of_task(int(task_id))
            if src is not None:
                provider, mid = src
                message_id = f"{provider}:{mid}"
        get_bus().publish(
            type_="tool_call",
            payload={
                "row_id": values.get("id"),
                "agent": values.get("agent"),
                "tool": values.get("tool"),
                "task_id": task_id,
                "failed": values.get("failed", False),
                "latency_ms": values.get("latency_ms"),
                "message_id": message_id,
            },
        )

    async def record_node_run(self, **values) -> None:
        """One attempt at one graph node — see `schema.NodeRun`.

        `reason` is scrubbed at the write for the reason `fail_outbound`'s
        error is: it is exception text, and a client's exception can quote
        the header it sent."""
        values.setdefault("created_at", _now())
        values["reason"] = scrub(values.get("reason") or "")
        async with self._sessions.begin() as session:
            session.add(schema.NodeRun(**values))

    async def node_runs(self, task_id: int) -> list[dict]:
        """Every recorded attempt at every node of this task's graphs, oldest
        first."""
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.NodeRun)
                .where(schema.NodeRun.task_id == task_id)
                .order_by(schema.NodeRun.id)
            )
            return [
                {
                    "dag_name": r.dag_name,
                    "dag_version": r.dag_version,
                    "node": r.node,
                    "attempt": r.attempt,
                    "status": r.status,
                    "reason": r.reason,
                    "duration_ms": r.duration_ms,
                    "created_at": r.created_at,
                }
                for r in rows
            ]

    async def source_message_of_task(self, task_id: int) -> tuple[str, str] | None:
        """`provider, provider_message_id` of the message that opened
        `task_id`. The SSE payload carries the row id of the call
        or tool, not the message; this lookup is what gives the
        Monitor screen the deep-link to the originating flow page.

        Returns `None` for tasks that pre-date the messages
        table having a `task_id` column, or for tasks that have
        no message attached (a manually-seeded plan)."""
        async with self._sessions() as session:
            result = await session.execute(
                select(
                    schema.Message.provider,
                    schema.Message.provider_message_id,
                )
                .where(schema.Message.task_id == task_id)
                .order_by(schema.Message.created_at)
                .limit(1)
            )
            row = result.first()
            if row is None:
                return None
            return row[0], row[1]

    async def tools_for_tasks(self, task_ids) -> dict[int, list[ToolCall]]:
        """What each of these tasks reached for, oldest first within a task.

        One query rather than one per task, for the reason `calls_for_tasks`
        gives: the board renders up to two hundred of them.
        """
        wanted = list(task_ids)
        if not wanted:
            return {}
        query = (
            select(schema.ToolCall)
            .where(schema.ToolCall.task_id.in_(wanted))
            .order_by(schema.ToolCall.created_at.asc(), schema.ToolCall.id.asc())
        )
        grouped: dict[int, list[ToolCall]] = {}
        async with self._sessions() as session:
            for row in await session.scalars(query):
                if row.task_id is None:  # excluded by the filter; narrows the type
                    continue
                grouped.setdefault(row.task_id, []).append(_tool_call(row))
        return grouped

    async def spent_today(self, agent: str | None = None) -> int:
        """Tokens spent since midnight UTC, in and out — by one agent, or by
        all of them when no name is given.

        Summed from the rows rather than counted in memory, so a restart does
        not forgive a budget and the number cannot drift from what the board
        shows. Per agent because they are different jobs against different
        models: the classifier running on every mention and the responder
        running on a few are not one pool, and a shared ceiling would let the
        cheap high-volume one exhaust the careful one.

        Midnight UTC rather than the operator's midnight. A budget needs a
        boundary that does not move, and the process has no opinion about
        where they are.
        """
        start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
        query = select(
            func.sum(schema.ModelCall.input_tokens + schema.ModelCall.output_tokens)
        ).where(schema.ModelCall.created_at >= start)
        if agent is not None:
            query = query.where(schema.ModelCall.agent == agent)
        async with self._sessions() as session:
            return int(await session.scalar(query) or 0)

    async def spent_today_by_agent(self) -> dict[str, int]:
        """Today's tokens, per agent, in one query.

        Grouped from the rows rather than asked once per name, because there
        is no list of names to ask for: which agents exist is `config.yaml`'s
        business, and a hardcoded list here would be wrong the first time
        somebody adds one.
        An agent that has not spent anything today is simply absent, which is
        the same answer as zero and does not require knowing it exists.
        """
        start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
        query = (
            select(
                schema.ModelCall.agent,
                func.sum(
                    schema.ModelCall.input_tokens + schema.ModelCall.output_tokens
                ),
            )
            .where(schema.ModelCall.created_at >= start)
            .group_by(schema.ModelCall.agent)
        )
        async with self._sessions() as session:
            rows = await session.execute(query)
            return {agent: int(total or 0) for agent, total in rows}

    async def calls_for_tasks(self, task_ids) -> dict[int, list[ModelCall]]:
        """Every call for each of these tasks, oldest first within a task.

        One query rather than one per task: the board renders up to two hundred
        of them, and asking per task made a page render cost two hundred round
        trips to answer a question about a table that is indexed on exactly
        this column.
        """
        wanted = list(task_ids)
        if not wanted:
            return {}
        query = (
            select(schema.ModelCall)
            .where(schema.ModelCall.task_id.in_(wanted))
            .order_by(schema.ModelCall.created_at.asc(), schema.ModelCall.id.asc())
        )
        grouped: dict[int, list[ModelCall]] = {}
        async with self._sessions() as session:
            for row in await session.scalars(query):
                if row.task_id is None:  # excluded by the filter; narrows the type
                    continue
                grouped.setdefault(row.task_id, []).append(_model_call(row))
        return grouped

    async def calls_for_task(self, task_id: int) -> list[ModelCall]:
        """Every call made while working on one task, oldest first.

        Oldest first because they read as a sequence — what was extracted,
        then what was drafted — and a reader following a task's history is
        going forwards.
        """
        query = (
            select(schema.ModelCall)
            .where(schema.ModelCall.task_id == task_id)
            .order_by(schema.ModelCall.created_at.asc(), schema.ModelCall.id.asc())
        )
        async with self._sessions() as session:
            return [_model_call(row) for row in await session.scalars(query)]

    async def calls_by_message(self, message_ids) -> dict[str, ModelCall]:
        """The most recent call about each of these messages.

        Asked for *by message* rather than by taking a page of recent calls and
        keying it: a page is shared by every agent, and only triage's rows
        carry a message id at all. Once the extractors, the responder and the
        summariser started recording, a page of the newest calls could be
        entirely rows that can never match a message while the call that
        classified it sat just outside the window.

        Newest wins where a message has more than one — a reclassification is
        what the board should show.
        """
        wanted = list(message_ids)
        if not wanted:
            return {}
        query = (
            select(schema.ModelCall)
            .where(schema.ModelCall.message_id.in_(wanted))
            .order_by(schema.ModelCall.created_at.asc(), schema.ModelCall.id.asc())
        )
        async with self._sessions() as session:
            return {
                row.message_id: _model_call(row)
                for row in await session.scalars(query)
                # Excluded by the filter above; stated so the type says it too.
                if row.message_id is not None
            }

    async def model_calls(
        self,
        *,
        message_id: str | None = None,
        #: Only the rows that name no message. Distinct from `message_id=None`,
        #: which means "do not filter" — and the difference is the whole reason
        #: this exists: after every agent started recording, most rows name no
        #: message and there was no way to ask for them.
        uncorrelated: bool = False,
        #: `None` is unbounded, the same spelling `outbound` already uses.
        #: Every HTTP caller passes a number — the routes cap it at `MAX_PAGE`
        #: — and the one caller that does not is `flow_for`, which is already
        #: narrowed to a single message and must not silently truncate the
        #: path it exists to assemble.
        limit: int | None = 50,
    ) -> list[ModelCall]:
        query = select(schema.ModelCall)
        if message_id is not None:
            query = query.where(schema.ModelCall.message_id == message_id)
        elif uncorrelated:
            query = query.where(schema.ModelCall.message_id.is_(None))
        async with self._sessions() as session:
            rows = await session.scalars(
                query.order_by(schema.ModelCall.created_at.desc(),
                               schema.ModelCall.id.desc()).limit(limit)
            )
            return [
                _model_call(row)
                for row in rows
            ]

    async def trim_model_calls(self, *, keep_days: float) -> int:
        """Prompts are large and nobody reads old ones. A container that never
        restarts would otherwise fill its volume with them."""
        async with self._sessions.begin() as session:
            result = await session.execute(
                delete(schema.ModelCall).where(
                    schema.ModelCall.created_at < _now() - timedelta(days=keep_days)
                )
            )
            return result.rowcount

    async def tone_examples(self, limit: int = 8) -> list[InboundEvent]:
        """The operator's own recent messages, for a model to learn a voice from.

        Excludes anything the agent sent: it goes out under the same account
        and comes back over the gateway indistinguishable from a real one, so
        without this the responder learns its own voice and amplifies it every
        round. Real examples carry a tone that a written style guide does not,
        which is the whole reason for reading them.
        """
        sent = select(schema.Outbound).where(
            schema.Outbound.state.in_((OUTBOUND_SENT, OUTBOUND_SENT_MANUALLY))
        ).subquery()
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

    async def counts(self) -> dict:
        """How much of everything there is, without materialising any of it."""
        async with self._sessions() as session:
            return {
                "messages": await session.scalar(
                    select(func.count()).select_from(schema.Message)
                ),
                "untriaged": await session.scalar(
                    select(func.count())
                    .select_from(schema.Message)
                    .where(
                        schema.Message.mention_type.is_not(None),
                        schema.Message.triaged_at.is_(None),
                    )
                ),
                "last_message_at": await session.scalar(
                    select(func.max(schema.Message.created_at))
                ),
                "tasks": dict(
                    (await session.execute(
                        select(schema.Task.state, func.count())
                        .group_by(schema.Task.state)
                    )).all()
                ),
                "outbound": dict(
                    (await session.execute(
                        select(schema.Outbound.state, func.count())
                        .group_by(schema.Outbound.state)
                    )).all()
                ),
            }

    # ---- memory ----------------------------------------------------------
    #
    # Ticket 09's D9: an agent writes its own memory and reads it back,
    # scoped to one channel by `FridayState`. What replaced the old
    # staging-and-promotion tier is enforced here, not in the tool layer —
    # the tool relays whatever it gets back, so a check that only lived there
    # would not survive a second caller.

    #: How many memories one channel may hold. Enforced here rather than in
    #: `friday/tools/memory.py`, which is the whole point: a store method is
    #: the only place that can actually stop a write, and a tool that merely
    #: checked would not survive a second caller reaching the store directly.
    #:
    #: At the cap, a write is refused rather than evicting the oldest row.
    #: Silently dropping *any* memory — oldest or not — is exactly the kind
    #: of loss this design otherwise refuses to produce without a trace: the
    #: operator can see what was written and by whom, but only because
    #: nothing else removes it first. Two hundred is a lot of one-sentence
    #: facts about one room; hitting it is itself a signal that something
    #: should be corrected or removed on purpose, which
    #: `memory_update`/`memory_delete` exist for.
    MEMORY_PER_CHANNEL = 200

    #: How long one memory's text may be, in characters. Same reasoning as
    #: `MEMORY_PER_CHANNEL`, for the same reason: `friday/tools/memory.py`
    #: already cuts to this length before writing, and a tool that merely
    #: checked would not survive a second caller reaching `memory_add` or
    #: `memory_update` directly. Cut, not refused, matching the tool's own
    #: policy (`_bounded`'s docstring) — a refusal here would cost a turn for
    #: nothing when the model has already said what it meant.
    TEXT_CHARS = 500

    async def memory_search(
        self, state: FridayState, query: str, *, kind: str, limit: int
    ) -> list[Memory]:
        """Every active match of this kind in this channel, newest first —
        not ranked by how well it matches, only by when it was written.

        `kind` is required, the same reasoning `limit` already got: the only
        caller (`friday/tools/memory.py`, scoped to the responder) always
        knows which kind it means — `MemoryKind.VOICE` — and a default here
        would let a second caller agree with that by coincidence rather than
        by saying so. Only `ACTIVE` rows match (D16): a superseded or deleted
        row is for the operator's own view, never a model's.

        `query` is matched the way `SkillLibrary.search` matches one of its
        ranks — every word has to appear somewhere in the text — but this
        does not rank: `SkillLibrary` scores a fixed catalogue read at
        startup, and a channel's memory changes underneath every call, so
        recency is the cheap, honest order rather than a score this method
        does not compute. Over a corpus that is a handful of rows per channel
        today; revisit if a room's memory ever grows past what a linear scan
        over its own rows can do cheaply.

        An empty `query` matches every word-count check vacuously and returns
        the channel's most recent memories up to `limit` — not validated
        against, because a model sending "" is a model that wants to see what
        is there, and that is a reasonable thing to want.
        """
        words = [w for w in query.lower().split() if w]
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Memory)
                .where(
                    schema.Memory.channel_id == state.channel_id,
                    schema.Memory.deleted_at.is_(None),
                    schema.Memory.status == MemoryStatus.ACTIVE,
                    schema.Memory.kind == kind,
                )
                .order_by(schema.Memory.created_at.desc())
            )
            matched = [
                row
                for row in rows
                if all(word in row.text.lower() for word in words)
            ]
            return [_memory(row) for row in matched[:limit]]

    async def domain_memories(self, channel_id: str) -> list[Memory]:
        """This channel's active domain-kind memories — fact, constraint,
        finding, decision — and the ones written for `channel_id = '*'`,
        newest first, for the extractor's per-call input (D14, D21). `voice`
        never reaches here; that kind is the responder's alone.

        `'*'` is "true everywhere", which is what `base.yaml` was, and the
        operator's rows are what a channel file's `overrides` were (board
        `read-it-the-way-the-operator-does`, ticket 10) — so this is the
        whole of what the extractor is told a room is.

        Not scoped by agent the way `memory_search` is, and takes no query:
        the extractor has no memory tools of its own (D21) and reads by
        injection, so there is no per-call search to filter by — only "what
        does this room's memory currently claim". Bounded the same way every
        other memory reader here is, by `MEMORY_PER_CHANNEL` on the write
        side rather than a limit here.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Memory)
                .where(
                    schema.Memory.channel_id.in_([channel_id, "*"]),
                    schema.Memory.deleted_at.is_(None),
                    schema.Memory.status == MemoryStatus.ACTIVE,
                    schema.Memory.kind.in_([k.value for k in DOMAIN_KINDS]),
                )
                .order_by(schema.Memory.created_at.desc())
            )
            return [_memory(row) for row in rows]

    async def room_summary(self, channel_id: str) -> Memory | None:
        """This room's active `summary` row, or `None` for a room nobody has
        summarised — what a channel file's `derived` section was (ticket 10).
        The partial unique index holds a room to one active summary, so there
        is never a second one to choose between."""
        async with self._sessions() as session:
            row = await session.scalar(
                select(schema.Memory).where(
                    schema.Memory.channel_id == channel_id,
                    schema.Memory.deleted_at.is_(None),
                    schema.Memory.status == MemoryStatus.ACTIVE,
                    schema.Memory.kind == MemoryKind.SUMMARY,
                )
            )
            return _memory(row) if row is not None else None

    async def knows_person(self, channel_id: str, discord_id: str) -> bool:
        """Whether the operator wrote this person down, for this room or for
        every room — an active `person` row keyed on their Discord id. What a
        channel file's `people:` map was, read by code and never shown to a
        model raw (ticket 10)."""
        async with self._sessions() as session:
            found = await session.scalar(
                select(schema.Memory.id).where(
                    schema.Memory.channel_id.in_([channel_id, "*"]),
                    schema.Memory.deleted_at.is_(None),
                    schema.Memory.status == MemoryStatus.ACTIVE,
                    schema.Memory.kind == MemoryKind.PERSON,
                    schema.Memory.key == discord_id,
                ).limit(1)
            )
            return found is not None

    #: How many findings one diagnosis reads — "the few matching", newest
    #: first. Enough to see a pattern; a runbook is what a longer one becomes.
    DIAGNOSE_FINDINGS = 5

    async def diagnose_memories(
        self,
        channel_id: str,
        *,
        service: str | None = None,
        error_code: str | None = None,
        path: str | None = None,
        text: str = "",
    ) -> list[Memory]:
        """What `Diagnose` reads about one case (spec, "Memory: one store,
        twelve kinds"): every active `fact`, `constraint` and `decision`; the
        runbooks whose `when` matches the case; and the few findings on the
        same `service:error_code`, newest first.

        This room's rows and the ones written for `channel_id = '*'`, which
        is "true everywhere". Ordered the way the spec orders a prompt —
        operator rows before model rows, runbooks after the domain kinds,
        findings last — so two cases on one service share a prefix.

        A runbook matches when any one of its `when` lists names the case:
        the service, the error code, a glob over the path, or a keyword
        found in the case's text. An empty `when` matches nothing — a
        runbook code cannot pick is one nobody asked for.
        """
        domain = [MemoryKind.FACT, MemoryKind.CONSTRAINT, MemoryKind.DECISION]
        async with self._sessions() as session:
            rows = list(
                await session.scalars(
                    select(schema.Memory)
                    .where(
                        schema.Memory.channel_id.in_([channel_id, "*"]),
                        schema.Memory.deleted_at.is_(None),
                        schema.Memory.status == MemoryStatus.ACTIVE,
                        schema.Memory.kind.in_(
                            [*domain, MemoryKind.RUNBOOK, MemoryKind.FINDING]
                        ),
                    )
                    .order_by(schema.Memory.created_at)
                )
            )
        admin_first = lambda row: row.origin != MemoryOrigin.ADMIN  # noqa: E731
        known = sorted((r for r in rows if r.kind in domain), key=admin_first)
        runbooks = [
            r for r in rows
            if r.kind == MemoryKind.RUNBOOK
            and _runbook_matches(r.data, service, error_code, path, text)
        ]
        findings = [
            r for r in reversed(rows)
            if r.kind == MemoryKind.FINDING and service is not None
            and (
                r.key == f"{service}:{error_code}"
                if error_code
                else (r.key or "").startswith(f"{service}:")
            )
        ][: self.DIAGNOSE_FINDINGS]
        return [_memory(r) for r in (*known, *runbooks, *findings)]

    async def memory_add(
        self,
        state: FridayState,
        text: str,
        *,
        kind: str = MemoryKind.VOICE,
        origin: str = MemoryOrigin.MODEL,
        key: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> Memory | None:
        """Write a new memory, or refuse if the channel is already full.

        `kind` defaults to `MemoryKind.VOICE` because the only wired producer
        today is the responder (`friday/tools/memory.py`), which writes
        nothing else — unlike `memory_search`'s `kind`, a default here names
        the one thing every caller before this ticket already meant, rather
        than standing in for a caller that forgot to say.

        `None` means the channel is at `MEMORY_PER_CHANNEL` — the caller
        (`friday/tools/memory.py`) turns that into a message the model can
        act on, the same way it turns a wrong-scope id into one. The count
        only considers active memories: a superseded or deleted row already
        freed its slot, the same rule `test_deleting_a_memory_frees_its_slot`
        pins for deletion.

        **The count and the insert happen under one lock**, because they are
        two awaits apart and two calls for one channel could otherwise both
        read 199 and both write. This said the race could not happen, since
        the pool drained its tasks one at a time; ticket 13 made the pool work
        tasks side by side, and a reaction marking a candidate right writes
        through here from the gateway whatever the pool is doing. One process
        and one `Database` (the constraint this whole store rests on), so an
        `asyncio.Lock` is the whole of it — not a database lock, which SQLite
        would only turn into a `database is locked` for one of the two.

        Checked against `check_not_instruction_shaped` before either the cap
        or the write (board `what-the-room-already-knows`, ticket 11, D25):
        this is the single write path every producer of a new memory shares,
        the operator's own hand included, so the check happens here once
        rather than being a rule each caller has to remember.

        **The same door checks a structured kind** (board
        `read-it-the-way-the-operator-does`, ticket 09): `data` is validated
        against the kind's schema (`MEMORY_DATA`) with the `fits` the harness
        uses on a model's answer, so a wrong-typed field is refused with the
        field named; the natural key is read off the validated data; and the
        origin must be one `writers_for(kind)` allows. Each refusal is a
        `MemoryRefused`. The instruction-shape guard reads `text` only —
        a structured payload is not a sentence.
        """
        if MemoryOrigin(origin) not in writers_for(kind):
            raise MemoryRefused(f"{kind} is not a kind {origin} may write")
        check_not_instruction_shaped(text)
        stored, key = _checked_data(kind, data, key)
        async with self._memory_slots, self._sessions.begin() as session:
            count = await session.scalar(
                select(func.count()).select_from(schema.Memory).where(
                    schema.Memory.channel_id == state.channel_id,
                    schema.Memory.deleted_at.is_(None),
                    schema.Memory.status == MemoryStatus.ACTIVE,
                )
            )
            if (count or 0) >= self.MEMORY_PER_CHANNEL:
                return None
            now = _now()
            row = schema.Memory(
                id=_memory_id(),
                channel_id=state.channel_id,
                agent=state.agent,
                text=text[: self.TEXT_CHARS],
                kind=kind,
                task_id=state.task_id,
                # The message that produced this memory, when the caller
                # supplies one. The Rooms screen joins on it.
                source_message_id=state.message_id,
                created_at=now,
                updated_at=now,
                origin=origin,
                key=key,
                data=stored,
            )
            session.add(row)
            await _flush_keyed(session, kind, key)
            return _memory(row)

    async def memory_update(
        self,
        state: FridayState,
        memory_id: str,
        text: str,
        *,
        data: dict[str, Any] | None = None,
        origin: str = MemoryOrigin.MODEL,
    ) -> Memory | None:
        """Correct a memory's wording in place — the same claim, said
        better — or `None` if this scope has no such (live, active) memory by
        this id: wrong channel, never existed, already deleted, and already
        superseded all read the same, on purpose.

        Distinct from `memory_supersede` (D16): this never changes what the
        memory claims, only how it is worded, so it never touches `kind`,
        `status` or `superseded_by`.

        Checked against `check_not_instruction_shaped` (ticket 11, D25) the
        same way `memory_add` is: a correction is a new line of text, and
        this is one of the write paths every producer of one shares.

        `data`, when given, replaces a structured row's payload and is
        checked as `memory_add` checks it; its natural key moves with it.
        Omitted, the payload is left as it is. A model-origin call never
        resolves an `origin=admin` row (`_live_memory`).
        """
        check_not_instruction_shaped(text)
        async with self._sessions.begin() as session:
            row = await self._live_memory(session, state, memory_id, origin)
            if row is None:
                return None
            if data is not None:
                row.data, row.key = _checked_data(row.kind, data, row.key)
            row.text = text[: self.TEXT_CHARS]
            row.updated_at = _now()
            await _flush_keyed(session, row.kind, row.key)
            return _memory(row)

    async def memory_supersede(
        self,
        state: FridayState,
        memory_id: str,
        text: str,
        *,
        origin: str = MemoryOrigin.MODEL,
        data: dict[str, Any] | None = None,
    ) -> Memory | None:
        """Replace what a memory claims, rather than correcting how it is
        worded (D16) — the operation `memory_update` deliberately is not.

        `data`, when given, is the new claim's payload for a structured
        kind, checked as `memory_add` checks it — the summariser's rebuild is
        this call, since a new summary is a new claim about the room (ticket
        10). Omitted, the replacement keeps the old row's payload.

        The old row is marked `SUPERSEDED` and points `superseded_by` at a
        freshly written row carrying the new claim, under the same `kind` so
        a reader that already trusts that kind's shape keeps trusting it. The
        old row's text is untouched: the board can still show what it used to
        say, and `updated_at` says when it changed.

        `None` for the same three reasons `memory_update` returns it — wrong
        channel, never existed, already deleted — plus a fourth: a row that
        is already superseded cannot be superseded again through this id.
        "The current one" is the row it points at, so supersede that one
        instead.

        Never refused for the channel's cap: an active row becomes inactive
        and a new active row is written in the same call, so the channel's
        active count does not move.

        Checked against `check_not_instruction_shaped` (ticket 11, D25) the
        same way `memory_add` and `memory_update` are: the new claim is a new
        line of text and this is one of the write paths every producer of
        one shares.
        """
        check_not_instruction_shaped(text)
        async with self._sessions.begin() as session:
            old = await self._live_memory(session, state, memory_id, origin)
            if old is None:
                return None
            stored, key = (
                _checked_data(old.kind, data, old.key)
                if data is not None
                else (old.data, old.key)
            )
            now = _now()
            new_row = schema.Memory(
                id=_memory_id(),
                channel_id=state.channel_id,
                agent=state.agent,
                text=text[: self.TEXT_CHARS],
                kind=old.kind,
                task_id=state.task_id,
                source_message_id=state.message_id,
                created_at=now,
                updated_at=now,
                status=MemoryStatus.ACTIVE,
                # The replacement keeps what the row is *about*; only the
                # claim in `text` changes. The old row goes inactive first
                # so the two never hold the key at once.
                origin=origin,
                key=key,
                data=stored,
            )
            old.status = MemoryStatus.SUPERSEDED
            old.superseded_by = new_row.id
            old.updated_at = now
            await session.flush()
            session.add(new_row)
            await session.flush()
            return _memory(new_row)

    async def memory_delete(
        self, state: FridayState, memory_id: str, *, origin: str = MemoryOrigin.MODEL
    ) -> bool:
        """Soft-delete: the row survives with who removed it and when, so an
        operator can see what a line said after it is gone. `False` for the
        same cases `memory_update` treats alike."""
        async with self._sessions.begin() as session:
            row = await self._live_memory(session, state, memory_id, origin)
            if row is None:
                return False
            row.deleted_at = _now()
            row.deleted_by = state.agent
            return True

    async def full_memory_channels(self) -> list[str]:
        """Every channel currently at `MEMORY_PER_CHANNEL` (board
        `what-the-room-already-knows`, ticket 12, D18). The cap itself
        already refuses a write there and evicts nothing — this is the other
        half: the condition is visible to the one party who can act on it,
        not only to the model that gets the refusal message. Sorted for a
        stable read, not by how full each one is — this is a short,
        occasional list, not a leaderboard.
        """
        async with self._sessions() as session:
            rows = await session.execute(
                select(schema.Memory.channel_id, func.count())
                .where(
                    schema.Memory.deleted_at.is_(None),
                    schema.Memory.status == MemoryStatus.ACTIVE,
                )
                .group_by(schema.Memory.channel_id)
                .having(func.count() >= self.MEMORY_PER_CHANNEL)
            )
            return sorted(channel_id for channel_id, _ in rows)

    async def memories_for_channel(
        self, channel_id: str, *, limit: int = 200
    ) -> list[Memory]:
        """This channel's memories, live or deleted, newest first — the
        operator's view. Not scope-filtered by agent: this is a human looking
        at one room, not a tool call from inside it.

        Bounded, unlike the room a caller might expect this to have: live
        memories are bounded by `MEMORY_PER_CHANNEL`, but a deleted row is
        never purged, so a channel that has churned through many corrections
        holds an unbounded number of rows this method would otherwise return
        every one of.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Memory)
                .where(schema.Memory.channel_id == channel_id)
                .order_by(schema.Memory.created_at.desc())
                .limit(limit)
            )
            return [_memory(row) for row in rows]

    async def _live_memory(
        self, session, state: FridayState, memory_id: str, origin: str
    ):
        """The row, if it exists, belongs to this scope, is not deleted, and
        is still active — the one query `memory_update`, `memory_supersede`
        and `memory_delete` share, so the reasons an id can fail to resolve
        cannot drift apart between them. A superseded row is frozen history
        (D16): correct or retract the row that replaced it, not this one.

        **A model-origin caller never resolves an operator's row** (ticket
        09): it gets the same `None` a wrong-scope id gets, so a model cannot
        tell an `origin=admin` row from one that does not exist. The
        operator may correct any row."""
        query = select(schema.Memory).where(
            schema.Memory.id == memory_id,
            schema.Memory.channel_id == state.channel_id,
            schema.Memory.deleted_at.is_(None),
            schema.Memory.status == MemoryStatus.ACTIVE,
        )
        if MemoryOrigin(origin) is not MemoryOrigin.ADMIN:
            query = query.where(schema.Memory.origin != MemoryOrigin.ADMIN)
        return await session.scalar(query)

    # ---- candidate memories (board `what-the-room-already-knows`,
    # ticket 12, D19, D20) --------------------------------------------------
    #
    # A second producer of memory, beside `memory_add`'s automatic write:
    # an agent proposes, and nothing reads the proposal back until the
    # operator marks it. `MemoryCandidate` is its own table rather than a
    # status on `memories` so that no reader of `memories` has to remember to
    # exclude a pending row — the guarantee D20 asks to hold structurally.

    async def propose_memory(
        self, state: FridayState, text: str, *, kind: str = MemoryKind.VOICE
    ) -> MemoryCandidate:
        """Stage a memory for the operator's mark rather than writing it.

        Not capped the way `memory_add` is: `MEMORY_PER_CHANNEL` bounds what
        a room's *memory* holds, and a candidate is not memory yet — the cap
        is enforced once, when `resolve_candidates_for_message` accepts one
        and calls `memory_add` for real.

        If `state.message_id` already carries a verdict — the operator
        marked this task's classification before this call ran — resolved
        immediately rather than left `PENDING` with no future reaction to
        ever trigger it: the reaction that would have resolved it already
        happened.
        """
        candidate_id = _memory_id()
        now = _now()
        async with self._sessions.begin() as session:
            row = schema.MemoryCandidate(
                id=candidate_id,
                channel_id=state.channel_id,
                agent=state.agent,
                text=text[: self.TEXT_CHARS],
                kind=kind,
                task_id=state.task_id,
                source_message_id=state.message_id,
                status=CandidateStatus.PENDING,
                proposed_at=now,
            )
            session.add(row)
            await session.flush()
            candidate = _candidate(row)

        if state.message_id is None:
            return candidate
        verdict = await self.verdict_for(
            provider="discord", provider_message_id=state.message_id
        )
        if verdict is None:
            return candidate
        mark, by = verdict
        return await self._resolve_candidate(candidate, mark=mark, by=by)

    async def resolve_candidates_for_message(
        self, *, provider_message_id: str, mark: str, by: str
    ) -> list[MemoryCandidate]:
        """Every `PENDING` candidate this message resolves, marked accepted
        or rejected by the same gesture that already confirms this message's
        classification (D19: "there is one thing to learn", not two) — call
        this alongside `record_verdict`, from the same reaction handler,
        never on its own.

        Accepting writes the candidate through `memory_add`, so ticket 11's
        refusal and `MEMORY_PER_CHANNEL`'s cap both still apply; either one
        refusing leaves the candidate `ACCEPTED` with `memory_id` still
        `None` rather than raising into a live reaction handler — the
        operator's judgement is recorded either way, the same way a
        `Verdict` records what they said independent of what a later pass
        does with it.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.MemoryCandidate).where(
                    schema.MemoryCandidate.source_message_id == provider_message_id,
                    schema.MemoryCandidate.status == CandidateStatus.PENDING,
                )
            )
            pending = [_candidate(row) for row in rows]
        return [
            await self._resolve_candidate(candidate, mark=mark, by=by)
            for candidate in pending
        ]

    async def _resolve_candidate(
        self, candidate: MemoryCandidate, *, mark: str, by: str
    ) -> MemoryCandidate:
        accepted = mark == "right"
        memory_id = None
        if accepted:
            state = FridayState(
                channel_id=candidate.channel_id,
                task_id=candidate.task_id,
                agent=candidate.agent,
                message_id=candidate.source_message_id,
            )
            try:
                written = await self.memory_add(state, candidate.text, kind=candidate.kind)
            except InstructionShaped as refused:
                log.warning(
                    "candidate %s accepted but refused at the write: %s",
                    candidate.id, refused,
                )
                written = None
            else:
                if written is None:
                    log.warning(
                        "candidate %s accepted but not written — channel %s "
                        "is at its cap",
                        candidate.id, candidate.channel_id,
                    )
                else:
                    memory_id = written.id
        async with self._sessions.begin() as session:
            row = await session.get(schema.MemoryCandidate, candidate.id)
            assert row is not None, f"candidate {candidate.id} vanished mid-resolve"
            row.status = CandidateStatus.ACCEPTED if accepted else CandidateStatus.REJECTED
            row.resolved_at = _now()
            row.resolved_by = by
            row.memory_id = memory_id
            await session.flush()
            return _candidate(row)

    async def candidates_for_channel(
        self, channel_id: str, *, limit: int = 200
    ) -> list[MemoryCandidate]:
        """Every candidate this channel has proposed, newest first, pending
        or resolved — the operator's own view, and the "place for a person
        to look" D19 says the old staging tier never had. A rejected
        candidate stays here rather than being deleted, so the operator can
        see what was proposed and turned down."""
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.MemoryCandidate)
                .where(schema.MemoryCandidate.channel_id == channel_id)
                .order_by(schema.MemoryCandidate.proposed_at.desc())
                .limit(limit)
            )
            return [_candidate(row) for row in rows]

    # ---- cursors -------------------------------------------------------

    async def advance_cursor(self, event: InboundEvent) -> None:
        """Move a channel's cursor forward to this message, never backward.

        Ids are numeric snowflakes, so age is a numeric comparison — as text,
        '99' would sort after '100' and look newer.
        """
        statement = insert(schema.Cursor).values(
            provider=event.provider,
            channel_id=event.channel_id,
            last_seen_message_id=event.provider_message_id,
        )
        async with self._sessions.begin() as session:
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[schema.Cursor.provider, schema.Cursor.channel_id],
                    set_={
                        "last_seen_message_id": statement.excluded.last_seen_message_id
                    },
                    where=cast(statement.excluded.last_seen_message_id, Integer)
                    > cast(schema.Cursor.last_seen_message_id, Integer),
                )
            )

    async def cursor_for(self, provider: str, channel_id: str) -> str | None:
        async with self._sessions() as session:
            return await session.scalar(
                select(schema.Cursor.last_seen_message_id).where(
                    schema.Cursor.provider == provider,
                    schema.Cursor.channel_id == channel_id,
                )
            )

    # ---- conversations -------------------------------------------------

    async def conversation_is_tracked(self, event: InboundEvent) -> bool:
        async with self._sessions() as session:
            return await session.scalar(
                select(schema.Conversation.id).where(
                    schema.Conversation.id == str(event.conversation)
                )
            ) is not None

    async def record_conversation(self, event: InboundEvent) -> bool:
        """Ensure the conversation exists. True if this created it."""
        statement = insert(schema.Conversation).values(id=str(event.conversation))
        async with self._sessions.begin() as session:
            result = await session.execute(statement.on_conflict_do_nothing())
            return result.rowcount == 1

    async def conversations(self) -> list[ConversationId]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Conversation.id).order_by(schema.Conversation.id)
            )
            return [ConversationId.parse(row) for row in rows]

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
        )
        async with self._sessions.begin() as session:
            session.add(row)
        return _outbound(row)

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
        """A failure that will be retried. Stays queued; the count is the bound."""
        async with self._sessions.begin() as session:
            await session.execute(
                update(schema.Outbound)
                .where(schema.Outbound.id == outbound_id)
                .values(
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
            rows = await session.scalars(query.order_by(schema.Outbound.id).limit(limit))
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
            return await session.scalar(
                select(func.count())
                .select_from(schema.Message)
                .where(
                    schema.Message.conversation_id == str(conversation),
                    cast(schema.Message.provider_message_id, Integer)
                    > cast(literal(message_id), Integer),
                )
            ) > 0

    async def _set_outbound(self, outbound_id: int, **values) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(schema.Outbound)
                .where(schema.Outbound.id == outbound_id)
                .values(**values)
            )

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
                .order_by(schema.Message.created_at)
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

    async def clear_dag_interruption(self, task_id: int) -> bool:
        """Withdraw a tool call waiting on the operator. Returns whether there
        was one.

        Only the `interruption` column: `paused_at_node` and
        `paused_question` are the record of what the run stopped on, and that
        stays true after the decision is moot. What must go is the *state a
        resume would run from*, because the tool executes the moment anyone
        approves it — so a patch left here outlives the work it belonged to
        (ticket 12).
        """
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(schema.DagState)
                .where(
                    schema.DagState.task_id == task_id,
                    schema.DagState.interruption.is_not(None),
                )
                .values(interruption=None, updated_at=_now())
            )
            return bool(result.rowcount)

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

    async def approve_outbound(self, outbound_id: int, *, by: str) -> None:
        """Record who approved this row and when. This is what the outbox
        selects on, and it releases this row and no other."""
        await self._set_outbound(outbound_id, approved_at=_now(), approved_by=by)

    async def outbound_row(self, outbound_id: int) -> Outbound | None:
        """One row by id, or None if there is no such row."""
        async with self._sessions() as session:
            row = await session.get(schema.Outbound, outbound_id)
            return _outbound(row) if row else None

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
        need pruning it is the same job as `keep_model_calls_days`, not a
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

    #: Two ineffective compactions and node 0 stops attempting a third —
    #: board `what-the-room-already-knows`, ticket 08, D6.
    COMPACTION_COOLDOWN_AFTER = 2

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

    async def dag_pauses(self, fingerprints: dict[int, str]) -> dict:
        """The node and question each of these tasks is paused on, if any.

        Keyed by task id to the fingerprint of that task's *current*
        parameters, and a pause computed against different ones is not
        returned. A pause is only cleared by a checkpoint, and a checkpoint
        only happens after a node completes — so a run that discards its state
        and then fails before finishing a node leaves the old question sitting
        there. Reporting it would ask the reporter the very thing they just
        answered, with their answer visible in the same message.

        `load_dag_state` has enforced this since the fingerprint landed. This
        is its sibling reading the same row, and one reader enforcing an
        invariant while the other ignores it is how the row starts lying.

        In bulk, because the caller has a batch and asking per task is how a
        poll that usually finds nothing costs twenty-one queries every two
        seconds.
        """
        if not fingerprints:
            return {}
        async with self._sessions() as session:
            rows = await session.execute(
                select(
                    schema.DagState.task_id,
                    schema.DagState.paused_at_node,
                    schema.DagState.paused_question,
                    schema.DagState.params_fingerprint,
                ).where(
                    schema.DagState.task_id.in_(list(fingerprints)),
                    schema.DagState.paused_at_node.is_not(None),
                )
            )
            return {
                task_id: (node, question or "")
                for task_id, node, question, fingerprint in rows
                if (fingerprint or "") == fingerprints[task_id]
            }

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
            said = await session.scalars(
                select(schema.Message.original_text)
                .where(
                    schema.Message.conversation_id == conversation_id,
                    schema.Message.author_id == author_id,
                    schema.Message.created_at >= opened_at,
                    schema.Message.provider_message_id.not_in(ours),
                )
                .order_by(schema.Message.created_at)
                .limit(limit)
            )
            texts = [text for text in said if text]
        if not texts:
            return None
        if budget_tokens is not None:
            while len(texts) > 1 and estimated_tokens("\n".join(texts)) > budget_tokens:
                texts = texts[1:]
        return "\n".join(texts) or None

    # ---- what the operator said about a classification -------------------

    async def record_verdict(
        self,
        *,
        provider: str,
        provider_message_id: str,
        mark: str,
        by: str,
    ) -> None:
        """Record that the operator marked this classification right or wrong.

        Upserted, so changing their mind replaces rather than accumulates.
        Marking the same thing twice leaves one row saying what they currently
        think, which is the only thing anything downstream wants to know.
        """
        statement = insert(schema.Verdict).values(
            provider=provider,
            provider_message_id=provider_message_id,
            mark=mark,
            marked_by=by,
            marked_at=_now(),
        )
        async with self._sessions.begin() as session:
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        schema.Verdict.provider,
                        schema.Verdict.provider_message_id,
                    ],
                    set_={
                        "mark": statement.excluded.mark,
                        "marked_by": statement.excluded.marked_by,
                        "marked_at": statement.excluded.marked_at,
                    },
                )
            )

    async def clear_verdict(
        self, *, provider: str, provider_message_id: str
    ) -> None:
        """The operator took the mark back. The row goes with it.

        Deleted rather than recorded as a third state, because "unmarked" and
        "never marked" mean the same thing to everything downstream: nobody
        is vouching for this one.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                delete(schema.Verdict).where(
                    schema.Verdict.provider == provider,
                    schema.Verdict.provider_message_id == provider_message_id,
                )
            )

    async def verdict_for(
        self, *, provider: str, provider_message_id: str
    ) -> tuple[str, str] | None:
        """The mark and who left it, or None if nobody has."""
        async with self._sessions() as session:
            row = await session.get(
                schema.Verdict, (provider, provider_message_id)
            )
            return (row.mark, row.marked_by) if row else None

    async def confirmed_classifications(
        self, *, limit: int = 20
    ) -> list[tuple[str, str]]:
        """Message text and the type it was marked *right* as.

        The join is the guarantee: only a classification the operator marked
        right appears here. One they never looked at is absent, and so cannot
        become an example the classifier learns its own habits from.

        Balanced across types rather than purely newest-first. Marks arrive in
        bursts — an afternoon spent confirming that a noisy channel is mostly
        `skip` is a realistic afternoon — and eight of eight examples reading
        "this one is skip" teaches the classifier to skip. Recency still
        orders *within* a type; what is shared out is the eight slots.
        """
        async with self._sessions() as session:
            rows = await session.execute(
                select(schema.Message.text, schema.Message.decision_type)
                .join(
                    schema.Verdict,
                    (schema.Verdict.provider == schema.Message.provider)
                    & (
                        schema.Verdict.provider_message_id
                        == schema.Message.provider_message_id
                    ),
                )
                .where(
                    schema.Verdict.mark == "right",
                    # A closed set, not merely "not null". `mark_triaged`
                    # also writes the *state* a message ended in — a
                    # low-confidence one is recorded as `needs_human` — and
                    # marking that right is a perfectly sensible thing for
                    # the operator to do. Showing it back as an example
                    # would teach the classifier a label it has no tool for.
                    schema.Message.decision_type.in_(DECISIONS),
                )
                .order_by(schema.Verdict.marked_at.desc())
                # Deeper than `limit`, because the balancing below picks from
                # this rather than taking it whole.
                .limit(max(limit * 4, limit))
            )
            return _balanced([(text, kind) for text, kind in rows], limit)

    # ---- workflow graph state -------------------------------------------

    async def save_dag_state(
        self,
        task_id: int,
        *,
        dag_name: str,
        results: dict,
        #: The path that produced those results. Saved with them because they
        #: are read together and are only meaningful together.
        trail: list[str] | None = None,
        #: Required, not defaulted. `_fingerprint` never returns "" — even for
        #: no parameters at all — so an empty one is only what a caller who
        #: forgot this argument writes, and writing it guarantees the next
        #: load throws the state away.
        params_fingerprint: str,
        #: `DAG.version` of the graph writing this. Required for the reason
        #: `params_fingerprint` is: a row saved without it matches no graph
        #: that asks with one, so every resume would silently start over.
        dag_version: str,
        paused_at_node: str | None = None,
        paused_question: str | None = None,
        #: The SDK's own run state, set only while a `needs_approval` tool
        #: call inside `paused_at_node` is waiting on the operator (ticket
        #: 07). `None` clears it — every ordinary checkpoint passes nothing,
        #: which is what makes approving (or a fresh pass discarding stale
        #: state) the only two ways it survives past the write that set it.
        interruption: dict | None = None,
    ) -> None:
        """Record what a task's workflow graph has produced so far.

        Upserted on `task_id`: one row per task, rewritten after every node.
        This is what makes a restart resume rather than start over, so it is
        written before the next node begins rather than at the end of the run.
        """
        statement = insert(schema.DagState).values(
            task_id=task_id,
            dag_name=dag_name,
            dag_version=dag_version,
            params_fingerprint=params_fingerprint,
            results=results,
            trail=list(trail or []),
            paused_at_node=paused_at_node,
            # A hand-over's reason, which can be a node's exception text.
            paused_question=(
                None if paused_question is None else scrub(paused_question)
            ),
            interruption=interruption,
            updated_at=_now(),
        )
        async with self._sessions.begin() as session:
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[schema.DagState.task_id],
                    set_={
                        "dag_name": statement.excluded.dag_name,
                        "dag_version": statement.excluded.dag_version,
                        "params_fingerprint": (
                            statement.excluded.params_fingerprint
                        ),
                        "results": statement.excluded.results,
                    "trail": statement.excluded.trail,
                        "paused_at_node": statement.excluded.paused_at_node,
                        "paused_question": statement.excluded.paused_question,
                        "interruption": statement.excluded.interruption,
                        "updated_at": statement.excluded.updated_at,
                    },
                )
            )

    async def load_dag_state(
        self,
        task_id: int,
        *,
        dag_name: str | None = None,
        params_fingerprint: str | None = None,
        dag_version: str | None = None,
    ) -> dict | None:
        """What the graph recorded, or None if it has not run.

        `dag_name` is the graph asking. When it does not match the one that
        wrote the row, nothing is returned and the graph starts over: node
        names are only meaningful inside the graph that defined them, and
        handing a renamed or rewritten graph its predecessor's results makes
        it skip nodes on the strength of work that was never done. A restart
        costs a few tool calls; a reply composed from another graph's
        findings is wrong in a way nobody can see.

        `params_fingerprint` is the same argument about the *inputs*. A node
        concluded what it concluded from the parameters it was given, and the
        whole point of asking the reporter for a correlationId is that the
        answer changes. State written before they answered says "there was
        nothing to look up", which is true of the old parameters and false of
        the new ones — and believing it means the graph asks a question,
        receives an answer, and then reads not one log line.

        Returns the raw results mapping; rebuilding it into a `DAGState` is
        the caller's business, so this module keeps knowing nothing about the
        graph. `load_dag_progress` returns the path alongside it, for the
        caller that resumes rather than only reads.
        """
        row = await self._dag_row(task_id, dag_name, params_fingerprint, dag_version)
        return None if row is None else dict(row.results or {})

    async def load_dag_progress(
        self,
        task_id: int,
        *,
        dag_name: str | None = None,
        params_fingerprint: str | None = None,
        dag_version: str | None = None,
    ) -> tuple[dict, list[str]] | None:
        """What the graph recorded, and the path that recorded it.

        One read for both, because they are only meaningful together and a
        resumed run needs both to answer "what did this graph decide?" — the
        results say which nodes have run, the path says in what order, and
        `Pool._outcome` reads the second backwards.
        """
        row = await self._dag_row(task_id, dag_name, params_fingerprint, dag_version)
        if row is None:
            return None
        return dict(row.results or {}), list(row.trail or [])

    async def _dag_row(
        self,
        task_id: int,
        dag_name: str | None,
        params_fingerprint: str | None,
        dag_version: str | None = None,
    ):
        """The row, once it has passed both tests of whether it is still
        anybody's to read. One place, so the two readers cannot disagree about
        when state is stale."""
        async with self._sessions() as session:
            row = await session.get(schema.DagState, task_id)
            if row is None:
                return None
            if dag_name is not None and row.dag_name != dag_name:
                log.info(
                    "task %d: discarding state from %r, this is %r",
                    task_id,
                    row.dag_name,
                    dag_name,
                )
                return None
            if dag_version is not None and row.dag_version != dag_version:
                # Same name, different shape: a node was renamed, added or
                # reordered. A result recorded under the old shape is not
                # this graph's, however its key happens to read.
                log.info(
                    "task %d: discarding state from %s version %s, this is %s",
                    task_id,
                    row.dag_name,
                    row.dag_version or "(none recorded)",
                    dag_version,
                )
                return None
            if (
                params_fingerprint is not None
                and (row.params_fingerprint or "") != params_fingerprint
            ):
                log.info(
                    "task %d: parameters are %s, the graph concluded against "
                    "%s — discarding what it concluded",
                    task_id,
                    params_fingerprint,
                    row.params_fingerprint or "(none recorded)",
                )
                return None
            return row

    async def dag_pause(self, task_id: int) -> tuple[str, str] | None:
        """The node that paused and the question it asked, if any."""
        async with self._sessions() as session:
            row = await session.get(schema.DagState, task_id)
            if row is None or not row.paused_at_node:
                return None
            return row.paused_at_node, row.paused_question or ""

    async def dag_interruption(self, task_id: int) -> dict | None:
        """Everything `decide_pending_action` needs to resume or decline a
        paused tool call — `None` unless one is actually waiting.

        Read raw rather than through `load_dag_state`, which filters by a
        fingerprint the caller does not have yet at this point: whether the
        stored state is still good for the task's *current* parameters is
        exactly what resuming has to check, not something to discard before
        the check runs.
        """
        async with self._sessions() as session:
            row = await session.get(schema.DagState, task_id)
            if row is None or row.interruption is None:
                return None
            return {
                "dag_name": row.dag_name,
                "results": dict(row.results or {}),
                "trail": list(row.trail or []),
                "params_fingerprint": row.params_fingerprint or "",
                "paused_at_node": row.paused_at_node,
                "interruption": row.interruption,
            }

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

    async def running_tasks(self) -> list[RunningTask]:
        """Tasks that are still being worked in, with the two
        activity fields the Monitor screen renders (`last_activity_at`,
        `last_tool`, `attempts`).

        Three round trips folded into one:
        - tasks in `OPEN` states, ordered by id
        - max `created_at` and `count(*)` from `model_calls`
        - the most recent `tool_calls.tool` for each task

        Two batched subqueries rather than one query per task, and
        one parent query rather than the N+1 the BoardScreen's older
        per-task pattern would have produced."""
        open_states = [s.value for s in OPEN]
        async with self._sessions() as session:
            # Most recent activity + count from model_calls, per task.
            model_subq = (
                select(
                    schema.ModelCall.task_id.label("task_id"),
                    func.max(schema.ModelCall.created_at).label("last_at"),
                    func.count(schema.ModelCall.id).label("n"),
                )
                .where(schema.ModelCall.task_id.is_not(None))
                .group_by(schema.ModelCall.task_id)
                .subquery()
            )
            # Most recent tool_calls.tool per task.
            tool_subq = (
                select(
                    schema.ToolCall.task_id.label("task_id"),
                    func.max(schema.ToolCall.created_at).label("last_at"),
                )
                .where(schema.ToolCall.task_id.is_not(None))
                .group_by(schema.ToolCall.task_id)
                .subquery()
            )
            # The parent join: tasks left-join both. SQLite honours
            # `coalesce` on `datetime`, so the row is the newer of
            # the two timestamps (or `None`).
            # The `messages` subquery finds the message that opened
            # the task — the operator's drill-down from the Monitor
            # screen to the Flow screen needs the source message id,
            # not the conversation id. One round trip, no per-task
            # lookup.
            message_subq = (
                select(
                    schema.Message.task_id.label("task_id"),
                    schema.Message.provider.label("provider"),
                    schema.Message.provider_message_id.label("provider_message_id"),
                )
                .order_by(schema.Message.created_at.asc())
                .subquery()
            )
            stmt = (
                select(
                    schema.Task,
                    func.coalesce(model_subq.c.last_at, tool_subq.c.last_at).label(
                        "last_activity_at"
                    ),
                    func.coalesce(model_subq.c.n, 0).label("attempts"),
                    tool_subq.c.last_at.label("tool_last_at"),
                    message_subq.c.provider.label("msg_provider"),
                    message_subq.c.provider_message_id.label("msg_id"),
                )
                .where(schema.Task.state.in_(open_states))
                .outerjoin(model_subq, model_subq.c.task_id == schema.Task.id)
                .outerjoin(tool_subq, tool_subq.c.task_id == schema.Task.id)
                .outerjoin(
                    message_subq,
                    message_subq.c.task_id == schema.Task.id,
                )
                .order_by(
                    func.coalesce(model_subq.c.last_at, tool_subq.c.last_at).desc().nullslast()
                )
            )
            rows = await session.execute(stmt)

        # The two-name-from-one-row problem: the `last_tool` is on
        # `tool_calls` but we only fetched its timestamp. One more
        # round trip with the timestamps we have, to keep this
        # monitor query from growing into a join with the full tool
        # row.
        last_tool_by_task: dict[int, str] = {}
        if rows:
            ts_pairs = [
                (row[0].id, row.tool_last_at)
                for row in rows
                if row.tool_last_at is not None
            ]
            if ts_pairs:
                # Pick the most recent tool_call.tool for each task
                # whose last activity is the same timestamp the
                # `tool_subq` aggregated. Cheaper than a window
                # function on SQLite.
                tool_stmt = (
                    select(schema.ToolCall.task_id, schema.ToolCall.tool, schema.ToolCall.created_at)
                    .where(
                        schema.ToolCall.task_id.in_({tid for tid, _ in ts_pairs}),
                        schema.ToolCall.created_at.in_({ts for _, ts in ts_pairs}),
                    )
                )
                async with self._sessions() as session:
                    for tid, tool, _ in await session.execute(tool_stmt):
                        last_tool_by_task[tid] = tool

        return [
            RunningTask(
                id=row[0].id,
                type=row[0].type,
                state=row[0].state,
                room=str(row[0].conversation_id),
                # `provider:provider_message_id` is the shape `/flow/`
                # accepts. The Monitor screen reads this verbatim to
                # build the deep-link; the wire shape is the URL
                # shape by design.
                message_id=(
                    f"{row.msg_provider}:{row.msg_id}"
                    if row.msg_provider and row.msg_id
                    else None
                ),
                last_activity_at=row.last_activity_at,
                last_tool=last_tool_by_task.get(row[0].id),
                attempts=row.attempts or 0,
            )
            for row in rows
        ]

    async def recent_events(self, limit: int = 100) -> list[MonitorEvent]:
        """The Monitor screen's live feed: a single ordered list of
        the last `limit` rows across `model_calls` and `tool_calls`,
        newest first. Two queries, merged in Python — the union
        of two indexed-by-`created_at` tables is a SQL `UNION ALL`,
        which on SQLite with this many rows is faster as two
        indexed reads than one UNION."""
        async with self._sessions() as session:
            model_rows = await session.execute(
                select(
                    schema.ModelCall.id,
                    schema.ModelCall.agent,
                    schema.ModelCall.latency_ms,
                    schema.ModelCall.attempt,
                    schema.ModelCall.created_at,
                )
                .order_by(schema.ModelCall.created_at.desc())
                .limit(limit)
            )
            tool_rows = await session.execute(
                select(
                    schema.ToolCall.id,
                    schema.ToolCall.agent,
                    schema.ToolCall.tool,
                    schema.ToolCall.latency_ms,
                    schema.ToolCall.failed,
                    schema.ToolCall.created_at,
                )
                .order_by(schema.ToolCall.created_at.desc())
                .limit(limit)
            )

        events: list[MonitorEvent] = []
        for row in model_rows:
            attempt = row.attempt or 1
            state = "retrying" if attempt > 1 else "done"
            events.append(
                MonitorEvent(
                    id=row.id,
                    type="model_call",
                    occurred_at=row.created_at,
                    agent=row.agent,
                    tool=None,
                    latency_ms=row.latency_ms,
                    state=state,
                )
            )
        for row in tool_rows:
            events.append(
                MonitorEvent(
                    id=row.id,
                    type="tool_call",
                    occurred_at=row.created_at,
                    agent=row.agent,
                    tool=row.tool,
                    latency_ms=row.latency_ms,
                    state="ok" if not row.failed else "failed",
                )
            )
        events.sort(key=lambda e: e.occurred_at, reverse=True)
        return events[:limit]

    async def monitor_snapshot(self) -> MonitorSnapshot:
        """One snapshot of what the Monitor screen asks for on mount.
        Counts come from the same queries the BoardScreen's footer
        uses; events and running_tasks come from `recent_events`
        and `running_tasks`. Spend is the day's total."""
        counts = await self.counts()
        events = await self.recent_events(limit=100)
        running = await self.running_tasks()
        spend_by_agent = await self.spent_today_by_agent()
        return MonitorSnapshot(
            status="connected",
            events=events,
            running_tasks=running,
            messages=counts["messages"],
            untriaged=counts["untriaged"],
            last_message_at=counts["last_message_at"],
            spend_today=sum(spend_by_agent.values()),
        )

    async def task(self, task_id: int) -> Task | None:
        """One task, freshest read — `decide_pending_action` needs current
        params, not the ones a stored graph state was checkpointed against."""
        async with self._sessions() as session:
            row = await session.get(schema.Task, task_id)
            return _task(row) if row is not None else None

    async def _tasks(self, query) -> list[Task]:
        async with self._sessions() as session:
            return [_task(row) for row in await session.scalars(query)]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _memory_id() -> str:
    """Opaque and sparse, not sequential.

    `secrets.token_hex` rather than the row's own autoincrement: a model that
    invents an id has to land on a string nobody would guess, not merely one a
    counter has not reached yet. Collisions are not handled with a retry loop
    — at this length, over the row counts one channel's memory will ever
    reach, the birthday bound on a collision is astronomically below the
    chance of the process crashing first, and a caller that somehow hit one
    would get an ordinary primary-key violation, not silent corruption.
    """
    return secrets.token_hex(6)


def _memory(row: schema.Memory) -> Memory:
    return Memory(
        id=row.id,
        channel_id=row.channel_id,
        agent=row.agent,
        text=row.text,
        kind=row.kind,
        task_id=row.task_id,
        source_message_id=row.source_message_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
        deleted_by=row.deleted_by,
        status=row.status,
        superseded_by=row.superseded_by,
        origin=row.origin,
        key=row.key,
        data=row.data,
    )


def _runbook_matches(
    data: dict[str, Any] | None,
    service: str | None,
    error_code: str | None,
    path: str | None,
    text: str,
) -> bool:
    when = (data or {}).get("when") or {}
    said = text.lower()
    return bool(
        (service and service in when.get("services", []))
        or (error_code and error_code in when.get("error_codes", []))
        or (path and any(fnmatch(path, p) for p in when.get("path_patterns", [])))
        or any(k.lower() in said for k in when.get("keywords", []) if k)
    )


def _checked_data(
    kind: str, data: dict[str, Any] | None, key: str | None
) -> tuple[dict[str, Any] | None, str | None]:
    """`data` checked against `kind`'s schema, and the natural key read off
    it — or `MemoryRefused` naming what did not fit. What is stored is the
    validated instance turned back into plain JSON, so an unknown key the
    checker dropped is not kept either."""
    schema_type = MEMORY_DATA[MemoryKind(kind)]
    if schema_type is None:
        if data:
            raise MemoryRefused(f"a {kind} is prose — it carries no data")
        return None, None
    if data is None and kind == MemoryKind.DECISION:
        return None, None
    if not isinstance(data, dict):
        raise MemoryRefused(f"a {kind} needs its data as an object")
    fitted, unfit = fits(data, schema_type)
    if unfit is not None:
        raise MemoryRefused(f"{kind} data does not fit: {unfit.why}")
    stored = asdict(fitted)
    return stored, natural_key(kind, stored, key)


async def _flush_keyed(session, kind: str, key: str | None) -> None:
    """Flush, turning the partial unique index's refusal into a sentence."""
    try:
        await session.flush()
    except IntegrityError:
        raise MemoryKeyTaken(
            f"an active {kind} {key!r} is already here — correct that one instead"
        ) from None


def _candidate(row: schema.MemoryCandidate) -> MemoryCandidate:
    return MemoryCandidate(
        id=row.id,
        channel_id=row.channel_id,
        agent=row.agent,
        text=row.text,
        kind=row.kind,
        task_id=row.task_id,
        source_message_id=row.source_message_id,
        status=row.status,
        proposed_at=row.proposed_at,
        resolved_at=row.resolved_at,
        resolved_by=row.resolved_by,
        memory_id=row.memory_id,
    )


def _artifact_id() -> str:
    """Opaque and sparse, the same reasoning `_memory_id` documents: a build
    that inlines an artifact by id must fail on an invented one rather than
    resolving to somebody else's."""
    return f"a{secrets.token_hex(6)}"


#: Cheap, deterministic shape-sniffing — no model, and deliberately never a
#: substring of `content` itself. A one-line `curl` is common enough that
#: "the first N characters" *is* the whole artifact for anything short,
#: which is exactly the leak D8 exists to close: a summariser reading a
#: description that quotes the correlationId it must never see is reading
#: the correlationId. `SQL_KEYWORDS` first because `SELECT` also starts a
#: plausible shell word; `TRACEBACK` is Python's own literal banner.
_SQL_KEYWORDS = ("select ", "insert ", "update ", "delete ", "create table")


def _describe_artifact(content: str) -> str:
    """One line, for a build that must not see the rest: what kind of thing
    this is and how big it is — never a copy, however short, of what it
    actually says. Not a summary — no model runs here."""
    lower = content.strip().lower()
    if lower.startswith("curl "):
        kind = "a curl"
    elif "traceback (most recent call last)" in lower:
        kind = "a stack trace"
    elif lower.startswith(_SQL_KEYWORDS):
        kind = "SQL"
    else:
        kind = "code"
    lines = content.count("\n") + 1
    plural = "" if lines == 1 else "s"
    return f"{kind}, {lines} line{plural}, {len(content)} chars"


def _artifact(row: schema.Artifact) -> Artifact:
    return Artifact(
        id=row.id,
        channel_id=row.channel_id,
        provider=row.provider,
        source_message_id=row.source_message_id,
        content=row.content,
        description=row.description,
        created_at=row.created_at,
    )


def _model_call(row: schema.ModelCall) -> ModelCall:
    """One row, as the domain sees it.

    Written out once rather than three times: the three readers of this table
    each built the dataclass by hand, so a column added to `schema` reached
    whichever of them somebody remembered.
    """
    return ModelCall(
        id=row.id,
        agent=row.agent,
        model=row.model,
        system_prompt=row.system_prompt,
        prompt=row.prompt,
        output=row.output,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        message_id=row.message_id,
        task_id=row.task_id,
        node=row.node,
        latency_ms=row.latency_ms,
        attempt=row.attempt or 1,
        created_at=row.created_at,
    )


def _tool_call(row: schema.ToolCall) -> ToolCall:
    """One row, as the domain sees it — for the reason `_model_call` gives.

    It was built by hand inside `tools_for_tasks`, which was the table's only
    reader, so it read as a local detail rather than a missing converter. It
    stopped being one the moment a second reader existed (`flow_for`), which
    is exactly the shape `_model_call`'s own docstring warns about.
    """
    return ToolCall(
        agent=row.agent,
        tool=row.tool,
        arguments=row.arguments,
        result=row.result,
        failed=bool(row.failed),
        latency_ms=row.latency_ms,
        message_id=row.message_id,
        task_id=row.task_id,
        node=row.node,
        created_at=row.created_at,
    )


def _event(row: schema.Message) -> InboundEvent:
    return InboundEvent(
        provider=row.provider,
        provider_message_id=row.provider_message_id,
        channel_id=row.channel_id,
        thread_id=row.thread_id,
        author_id=row.author_id,
        author_name=row.author_name,
        text=row.text,
        created_at=row.created_at,
        mention_type=MentionType(row.mention_type) if row.mention_type else None,
        is_own=row.is_own,
        reply_to=row.reply_to,
        task_id=row.task_id,
        # `is_enrichment` requires a join against `memories`; the cheap path
        # is the `_events` helper, which loads it once per row rather than
        # N+1. Rows that the helper did not enrich leave the field False
        # — the API renders the same shape regardless.
        is_enrichment=False,
    )


def _outbound(row: schema.Outbound) -> Outbound:
    return Outbound(
        id=row.id,
        task_id=row.task_id,
        conversation=ConversationId.parse(row.conversation_id),
        kind=row.kind,
        sender=row.sender,
        text=row.text,
        reply_to=row.reply_to,
        state=row.state,
        attempts=row.attempts,
        last_error=row.last_error,
        approves=row.approves,
    )


def _task(row: schema.Task) -> Task:
    return Task(
        id=row.id,
        conversation=ConversationId.parse(row.conversation_id),
        type=row.type,
        state=row.state,
        confidence=row.confidence,
        params=row.params or {},
        created_at=row.created_at,
        # The two fields below are populated by `running_tasks()`,
        # which loads them with a single batched query rather than
        # one round trip per task. The store-level converter fills
        # them with None / 0 here so the same shape serves readers
        # that did not ask for the activity rollup.
        last_activity_at=None,
        attempts=0,
    )


def _balanced(rows: list[tuple[str, str]], limit: int) -> list[tuple[str, str]]:
    """Share the example slots out across the types, newest first within each.

    Round-robin over the types present, taking the newest unused example of
    each in turn. A type nobody has confirmed simply is not in the rotation —
    this balances what exists rather than inventing what does not.

    Order is not preserved overall, and does not need to be: these go into the
    prompt as a set of labelled examples, not as a transcript.
    """
    by_type: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        by_type.setdefault(row[1], []).append(row)

    taken: list[tuple[str, str]] = []
    while len(taken) < limit and any(by_type.values()):
        for remaining in by_type.values():
            if not remaining:
                continue
            taken.append(remaining.pop(0))
            if len(taken) == limit:
                break
    return taken
