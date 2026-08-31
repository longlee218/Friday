"""The tables, as mapped classes.

Persistence types, not domain types. `friday.models` holds the frozen
dataclasses the rest of the system passes around; these exist only so
`friday.db` can talk to SQLite without hand-writing column lists — which is how
`is_own` once came to be stored in one table and not the other.

Nothing outside `friday.db` imports this module.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, String, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

__all__ = ["Base", "Conversation", "Cursor", "Message", "ModelCall", "Note", "Observation", "Outbound", "Task"]


class IsoDateTime(TypeDecorator):
    """Timezone-aware datetimes stored as ISO 8601 text.

    SQLAlchemy's own DateTime writes SQLite a naive format that drops the
    offset. Everything here is UTC and says so, and the existing rows are
    already ISO strings — parsing them back has to give the same instant.

    Naive values are rejected rather than assumed: guessing at a missing offset
    is how a message ends up hours out of place in the context a model reads.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"naive datetime: {value!r}. Everything here is UTC.")
        # Normalised to UTC, not just serialised: ORDER BY sorts this column as
        # text, so a row written at +07:00 would sort by its wall clock rather
        # than its instant and land in the wrong place in a conversation.
        return value.astimezone(timezone.utc).isoformat()

    def process_result_value(self, value: str | None, dialect) -> datetime | None:
        return datetime.fromisoformat(value) if value is not None else None


class Base(DeclarativeBase):
    pass


class Message(Base):
    """Every message seen, addressed to us or not.

    `mention_type` is what separates the two, and a non-null one that has not
    been triaged is the queue. One table: they were once two, and every
    in-scope mention was written to both, so a column added to one silently
    went missing from the other.
    """

    __tablename__ = "messages"

    provider: Mapped[str] = mapped_column(primary_key=True)
    provider_message_id: Mapped[str] = mapped_column(primary_key=True)
    channel_id: Mapped[str]
    thread_id: Mapped[str | None]
    #: Where the exchange is happening: the thread if there is one, else the
    #: channel. Stored rather than derived so every query can use it directly.
    conversation_id: Mapped[str] = mapped_column(index=True)
    author_id: Mapped[str]
    author_name: Mapped[str]
    text: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(IsoDateTime)
    is_own: Mapped[bool] = mapped_column(default=False)
    mention_type: Mapped[str | None]
    #: The `provider_message_id` this replies to, if any. Read at context-
    #: assembly time to tell a reply to the operator from unrelated traffic.
    reply_to: Mapped[str | None]
    #: Null until triage has looked at this message.
    triaged_at: Mapped[datetime | None] = mapped_column(IsoDateTime)
    task_id: Mapped[int | None]
    #: Every decision, including the ones that open no task. A skip leaves no
    #: other trace, and without it the threshold can only be guessed.
    decision_type: Mapped[str | None]
    decision_confidence: Mapped[float | None]
    decision_params: Mapped[dict | None] = mapped_column(JSON)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(index=True)
    type: Mapped[str]
    state: Mapped[str] = mapped_column(index=True)
    confidence: Mapped[float]
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(IsoDateTime)
    #: Approval is a fact about the work, not about a message. The outbox
    #: joins this rather than each caller checking it.
    approved_at: Mapped[datetime | None] = mapped_column(IsoDateTime)
    approved_by: Mapped[str | None]


class Cursor(Base):
    """How far each channel has been read.

    The sweep asks for messages after this point, so it must only ever move
    forward: it replays old messages after newer live ones, and a rewind would
    re-fetch the same window on every pass.
    """

    __tablename__ = "channel_cursors"

    provider: Mapped[str] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(primary_key=True)
    last_seen_message_id: Mapped[str]


class Conversation(Base):
    """Conversations that have involved us — not a copy of every channel.

    Keyed by the resolved id rather than by its parts: it is one value
    everywhere else, and two representations of the same thing is how they
    drift.
    """

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(primary_key=True)


class Outbound(Base):
    """One thing to send. Written when the workflow decides, not when it is
    approved — so a draft, its approval and its delivery are one row."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    #: Null for an alert: it is about the system, not about work.
    task_id: Mapped[int | None] = mapped_column(index=True)
    conversation_id: Mapped[str]
    kind: Mapped[str]
    #: Which identity speaks. Not the conversation's provider: the bot and the
    #: user account are two senders in one Discord conversation.
    sender: Mapped[str]
    text: Mapped[str]
    reply_to: Mapped[str | None]
    state: Mapped[str] = mapped_column(index=True, default="queued")
    #: The message this became once the platform accepted it. It comes back
    #: to us over the gateway as one of our own, and this is what tells the
    #: two apart afterwards.
    sent_message_id: Mapped[str | None] = mapped_column(index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None]
    #: Held back until this passes. Retrying a rate-limited send at once is
    #: how a rate limit becomes a ban.
    retry_after: Mapped[datetime | None] = mapped_column(IsoDateTime)
    created_at: Mapped[datetime] = mapped_column(IsoDateTime)
    sent_at: Mapped[datetime | None] = mapped_column(IsoDateTime)


class ModelCall(Base):
    """Both sides of one model call, kept so a decision can be explained after
    the fact rather than only while the process is alive."""

    __tablename__ = "model_calls"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    #: The message the call was made about — what links a decision to the
    #: prompt behind it.
    message_id: Mapped[str | None] = mapped_column(index=True)
    agent: Mapped[str]
    model: Mapped[str]
    system_prompt: Mapped[str]
    prompt: Mapped[str]
    output: Mapped[str]
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, index=True)


class Observation(Base):
    """Something a step learned. Staged, never read back into a prompt."""

    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(index=True)
    category: Mapped[str]
    text: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(IsoDateTime)
    #: Set by the compaction pass when an approved outcome corroborated it.
    #: Ticket 10; named here so the shape is complete rather than migrated later.
    promoted_at: Mapped[datetime | None] = mapped_column(IsoDateTime)


class Note(Base):
    """Something believed for longer than one task.

    Keyed on (category, text) so the same thing learned twice is one note with
    more support behind it, rather than two notes saying it.
    """

    __tablename__ = "notes"

    category: Mapped[str] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(primary_key=True)
    #: How many approved tasks agree. What decides which notes survive a trim.
    support: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(IsoDateTime)
