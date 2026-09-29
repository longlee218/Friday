"""The tables, as mapped classes.

Persistence types, not domain types. `friday.models` holds the frozen
dataclasses the rest of the system passes around; these exist only so
`friday.db` can talk to SQLite without hand-writing column lists — which is how
`is_own` once came to be stored in one table and not the other.

Nothing outside `friday.db` imports this module.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Index, String, TypeDecorator
from sqlalchemy import text as sql_text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

__all__ = [
    "AuditEntry",
    "Base",
    "Conversation",
    "Cursor",
    "DagState",
    "Memory",
    "MemoryCandidate",
    "Message",
    "ModelCall",
    "NodeRun",
    "Outbound",
    "StepResult",
    "Task",
    "ToolCall",
    "Verdict",
]


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
        return value.astimezone(UTC).isoformat()

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
    #: The raw text the reporter wrote. Stored so the workflow's extraction
    #: agent can read it without re-fetching from Discord; the extraction
    #: pass moved from triage to workflow in ticket 31, and the workflow needs
    #: the text to extract from.
    original_text: Mapped[str]
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
    #: `text`, with each verbatim span this message carried (a curl, a stack
    #: trace) swapped for a reference to the `Artifact` it became — never
    #: the content itself (board `what-the-room-already-knows`, ticket 07,
    #: D8). Written once, at record time, by whichever code split the
    #: message; `None` for a message with no code to split, or one recorded
    #: before this column existed. `relevant_messages_in_channel` — the
    #: summariser's own read, and its only caller — reads this in
    #: preference to `text`; every other reader keeps reading `text` or
    #: `original_text` unchanged.
    redacted_text: Mapped[str | None] = mapped_column(default=None)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(index=True)
    type: Mapped[str]
    state: Mapped[str] = mapped_column(index=True)
    confidence: Mapped[float]
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(IsoDateTime)
    #: The spine pass this task is on: the workflow id is
    #: `task-<id>/pass-<pass_no>`. +1 in the same transaction as every move
    #: into `pending` (build-the-spine ticket 14).
    pass_no: Mapped[int] = mapped_column(default=1, server_default="1")
    #: Why the current pass started: `first`, `reply` (from
    #: `waiting_for_details`, or a reporter message mid-pass), `hand_back`
    #: (from `needs_human`) or `reopen` (any other move into `pending`).
    pass_cause: Mapped[str] = mapped_column(default="first", server_default="first")


class Verdict(Base):
    """What the operator said about one classification.

    One row per message, replaced when they change their mind and deleted
    when they take the mark back — so the table says what they currently
    think rather than accumulating a history of reactions nobody reads.

    Its absence is the important state: a classification nobody marked is
    one nobody read, and it is never used as an example. Silence is not
    approval.
    """

    __tablename__ = "verdicts"

    provider: Mapped[str] = mapped_column(primary_key=True)
    provider_message_id: Mapped[str] = mapped_column(primary_key=True)
    #: "right" or "wrong". A closed set; see `friday.kernel.memory.verdicts.Mark`.
    mark: Mapped[str]
    #: Who marked it, as the platform names them.
    marked_by: Mapped[str]
    marked_at: Mapped[datetime] = mapped_column(IsoDateTime)


class DagState(Base):
    """What a task's workflow graph has produced so far.

    One row per task, rewritten after every node. Its own table rather than a
    column on `tasks` because the write rhythms differ: a task row changes at
    state transitions, this changes every few seconds while a graph is
    running, and mixing them means every checkpoint rewrites the row the board
    and the outbox are reading.
    """

    __tablename__ = "dag_state"

    task_id: Mapped[int] = mapped_column(primary_key=True)
    #: Which DAG produced this. A state written by one graph is not readable
    #: by another, and recording the name is how a rename is caught.
    dag_name: Mapped[str]
    #: A fingerprint of the task parameters the graph ran against. Results
    #: are only meaningful for the inputs that produced them: when the
    #: reporter supplies the correlationId the graph asked for, every
    #: conclusion drawn without it is stale, including the ones that
    #: concluded "nothing to look up".
    params_fingerprint: Mapped[str] = mapped_column(default="")
    #: Node name -> that node's result.
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    #: The nodes this graph has walked, in the order it walked them. Beside
    #: the results rather than derived from them: declaration order is not
    #: execution order, and `Pool._outcome` reads the path backwards to find
    #: which node decided. A resumed run that started from an empty path
    #: could only answer that from the part it happened to walk itself.
    #: Nullable, and that is not laziness: SQLite cannot add a NOT NULL
    #: column to a table that has rows, and this one has them on every
    #: deployment that has ever run a graph. A row written before this column
    #: existed has no recorded path, which is a different thing from an empty
    #: one, and both read as "nothing to resume from".
    trail: Mapped[list | None] = mapped_column(JSON, default=list)
    #: Set when a run ends on an `Ask` or `HandOver` a node returned to stop
    #: things there rather than decide the graph's own answer. Cleared on
    #: resume — the next node to run checkpoints without them.
    paused_at_node: Mapped[str | None]
    paused_question: Mapped[str | None]
    #: The SDK's own run state (ticket 07), set only while a tool call inside
    #: `paused_at_node` is waiting for the operator's yes or no — a dangerous
    #: action, never a message; those wait at the outbox instead. Approving
    #: resumes this exact call, in this process or a later one, rather than
    #: re-running the node from scratch. Cleared alongside the pause columns.
    interruption: Mapped[dict | None] = mapped_column(JSON, default=None)
    updated_at: Mapped[datetime] = mapped_column(IsoDateTime)


class NodeRun(Base):
    """One attempt at one graph node, as the workflow adapter's `_invoke` saw
    it end.

    A row per attempt, not per node, for the reason `model_calls` holds a row
    per provider call: three tries recorded as one outcome is the record
    disagreeing with what happened. Append-only; nothing reads it back to
    decide anything — resume is DBOS's now (ticket 06), off its own step
    memoization, not this table.
    """

    __tablename__ = "node_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    #: `None` for a graph run with no task, which only a test does today.
    task_id: Mapped[int | None] = mapped_column(index=True)
    dag_name: Mapped[str]
    node: Mapped[str]
    #: 1 for the first try. An ordinal, not a total.
    attempt: Mapped[int]
    #: The result's envelope status — ok, empty, skipped, timed_out, error.
    status: Mapped[str]
    reason: Mapped[str] = mapped_column(default="")
    duration_ms: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, index=True)


class StepResult(Base):
    """What one spine step came to, keyed by its content (build-the-spine
    ticket 12; board `domains-plug-in` tickets 09 §8, 10 §8, 13 §6).

    Keyed by `(task_id, step_key)`, not by plan version: a replan's identical
    step finds its result here and is not run again. Written by the runner
    alone (`friday/kernel/spine/runner.py`), never rewritten — a stored
    result is the reuse rule. No runtime twin: the runner reads these rows.
    """

    __tablename__ = "step_results"

    task_id: Mapped[int] = mapped_column(primary_key=True)
    step_key: Mapped[str] = mapped_column(primary_key=True)
    #: The pass that stored it (ticket 14). A key holds one row per pass at
    #: most: a stored `Ask` is continued, and a stored `HandOver` re-run, by
    #: a later pass, whose result lands beside it; the newest row is the one
    #: the runner reads.
    pass_no: Mapped[int] = mapped_column(
        primary_key=True, default=1, server_default="1"
    )
    #: The step's id and plan version when it ran — for the board, not a key.
    step_id: Mapped[str]
    plan_version: Mapped[int]
    #: `result | reply | ask | hand_over | replan | retriage`.
    kind: Mapped[str]
    #: The result as JSON data; an `Ask` without its `Evidence` (ticket 14).
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(IsoDateTime)


class PlanVersion(Base):
    """One version of a task's plan, frozen or refused (build-the-spine
    ticket 14; board `domains-plug-in` tickets 11 and 14 §6). Idempotent on
    `(task_id, version)`: a crash that re-runs the Planner keeps the first
    version stored. The board reads these; the pass reads the newest frozen
    one back to continue or replan from.
    """

    __tablename__ = "plans"

    task_id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    #: The hash of the version this one replaced; `None` for version 1.
    replaces: Mapped[str | None]
    #: `first | replan | reply | hand_back` — what made the Planner write it.
    #: `replan` and `reply` count toward the contract's `max_replans`, from
    #: the last `hand_back` on.
    cause: Mapped[str]
    #: The task's placement identity when it was planned, as JSON; a pass
    #: whose Intake differs replans.
    placement: Mapped[list] = mapped_column(JSON)
    #: The plan as JSON — the frozen one, or the last one refused; `None`
    #: when the Planner never answered.
    body: Mapped[dict | None] = mapped_column(JSON)
    #: `plan_hash` when GatePlan froze it; `None` when refused.
    hash: Mapped[str | None]
    #: A refused version's errors, one list per try, in order; `[]` for a
    #: frozen one.
    gate_errors: Mapped[list] = mapped_column(JSON, default=list)
    pass_no: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(IsoDateTime)


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
    #: What the operator calls this room. Theirs alone: nothing assembles it
    #: into a prompt and no agent reads it, so it carries none of the
    #: escaping obligations a context value does. It exists because a Discord
    #: channel id is nineteen digits, the provider is never asked for a name,
    #: and a person cannot manage work in rooms they cannot tell apart.
    name: Mapped[str | None] = mapped_column(default=None)


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
    #: Approval is a fact about this row, not about its task. It was on the
    #: task, written once and never cleared, so approving the first reply
    #: approved every reply queued after it — including the one that asserts
    #: a cause. The outbox reads these rather than each caller checking.
    approved_at: Mapped[datetime | None] = mapped_column(IsoDateTime)
    approved_by: Mapped[str | None]
    #: The message that was approved, hashed — set when the row becomes sendable
    #: (at enqueue for a policy-approved kind, at approval for a reply). The
    #: outbox recomputes it at dispatch: a mismatch means the text changed after
    #: approval, so the approval is void and the row goes to the operator.
    approved_payload_hash: Mapped[str | None]
    #: On an approval card, the row it asks about — so pressing the button
    #: approves that reply and nothing queued after it.
    approves: Mapped[int | None]


class ModelCall(Base):
    """Both sides of one model call, kept so a decision can be explained after
    the fact rather than only while the process is alive."""

    __tablename__ = "model_calls"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    #: The message the call was made about — what links a decision to the
    #: prompt behind it. Right for triage and for nothing downstream, which is
    #: why the next two exist.
    message_id: Mapped[str | None] = mapped_column(index=True)
    #: The task whose work this call was part of, and the graph node that made
    #: it. Indexed because "what did this task cost, and what was it asked?"
    #: is the question the board is for.
    task_id: Mapped[int | None] = mapped_column(index=True)
    node: Mapped[str | None] = mapped_column(default=None)
    #: Wall clock, in milliseconds.
    latency_ms: Mapped[int | None] = mapped_column(default=None)
    #: Which attempt of its run this call was, 1-based. Nullable for the same
    #: reason `trail` is: SQLite cannot add a NOT NULL column to a table that
    #: has rows, and this one has them wherever a model has ever been called.
    #: A row written before the column existed says nothing about attempts,
    #: which is different from saying it was the first.
    attempt: Mapped[int | None] = mapped_column(default=1)
    agent: Mapped[str]
    model: Mapped[str]
    system_prompt: Mapped[str]
    prompt: Mapped[str]
    output: Mapped[str]
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, index=True)


class Memory(Base):
    """Something an agent chose to remember, scoped to one channel.

    `id` is a string rather than the usual autoincrementing integer: it is
    opaque and sparse on purpose, generated in the store rather than left to
    the database's own sequence, so a model that invents one fails instead of
    resolving to whichever row happens to sit at that offset.

    Soft-deleted: `memory_delete` sets `deleted_at`/`deleted_by` rather than
    removing the row, so the operator can see what a line said and who took it
    out. Every reader that serves a model treats a deleted row as absent.
    """

    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(index=True)
    agent: Mapped[str]
    text: Mapped[str]
    #: A registered memory kind, stored as its string, the way `Task.state`
    #: stores `TaskState` (a validated string since ticket 12 —
    #: `friday.kernel.memory.registry.validate_kind`). Who reads a row is a function of
    #: this value (`readers_for`), never a second column.
    kind: Mapped[str]
    task_id: Mapped[int | None] = mapped_column(index=True)
    #: The message that produced this memory. Set by memory_add from the
    #: FridayState.message_id field; the Rooms screen joins on it to
    #: mark the source row with an enrichment glyph. Nullable because
    #: older rows were written before this link existed, and a
    #: backfill is a guess the operator would have to audit by hand.
    source_message_id: Mapped[str | None] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, index=True)
    updated_at: Mapped[datetime] = mapped_column(IsoDateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(IsoDateTime, default=None)
    deleted_by: Mapped[str | None] = mapped_column(default=None)
    #: A memory's lifecycle (D16): `"active"` unless a later memory
    #: replaced this one's claim, in which case `"superseded"` and
    #: `superseded_by` names the row that replaced it. The migration backs
    #: this with a server default of `"active"` for any pre-existing row.
    status: Mapped[str] = mapped_column(default="active")
    superseded_by: Mapped[str | None] = mapped_column(default=None)
    #: `friday.sdk.memory.MemoryOrigin`: `"model"` or `"admin"`. The
    #: migration backs it with a server default of `"model"`, which is what
    #: every row written before the column existed was.
    origin: Mapped[str] = mapped_column(default="model")
    #: A structured kind's natural key; null for prose.
    key: Mapped[str | None] = mapped_column(default=None)
    #: A structured kind's payload, checked against its schema at write time.
    data: Mapped[dict | None] = mapped_column(JSON, default=None)

    #: One active row per `(channel, kind, key)` — a second `service` named
    #: `reelme-order` is a conflict, not a second opinion. Partial, so a
    #: superseded or deleted row keeps its key as history, and a prose row's
    #: null key never collides (SQLite treats nulls as distinct).
    #:
    #: `finding` is left out. Its key is `service:error_code`, which names
    #: the fault rather than the finding: every diagnosis of a known fault
    #: writes its own, and several saying the same thing are the spec's
    #: signal that a runbook is owed. `Database.case_memories` matches a
    #: finding on its data (`service`), not on this key.
    __table_args__ = (
        Index(
            "uq_memories_active_key",
            "channel_id",
            "kind",
            "key",
            unique=True,
            sqlite_where=sql_text(
                "status = 'active' AND deleted_at IS NULL AND kind != 'finding'"
            ),
        ),
    )


class MemoryCandidate(Base):
    """A memory an agent proposed, waiting for the operator's mark (board
    `what-the-room-already-knows`, ticket 12, D19, D20).

    Its own table rather than a `Memory` row with a `"pending"` status: every
    reader that serves a model would need to remember to filter it out, which
    is exactly the kind of guarantee D20 says has to hold structurally, not by
    everyone remembering a `WHERE`.
    """

    __tablename__ = "memory_candidates"

    id: Mapped[str] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(index=True)
    agent: Mapped[str]
    text: Mapped[str]
    kind: Mapped[str]
    task_id: Mapped[int | None] = mapped_column(index=True)
    #: The message that resolves this candidate — the same one the operator
    #: reacts to in order to mark the classification that opened this task.
    source_message_id: Mapped[str | None] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(default="pending", index=True)
    proposed_at: Mapped[datetime] = mapped_column(IsoDateTime, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(IsoDateTime, default=None)
    resolved_by: Mapped[str | None] = mapped_column(default=None)
    #: Set only once accepted, and only if the write actually landed — see
    #: `friday.kernel.domain.memory.MemoryCandidate`'s own docstring for why it can
    #: stay `None` on an accepted row.
    memory_id: Mapped[str | None] = mapped_column(default=None)


class Artifact(Base):
    """Verbatim material a message carried — code, a stack trace, a curl —
    stored whole (board `what-the-room-already-knows`, ticket 07, D8).

    Written once, by the store, at the moment the message that carried it is
    first recorded; nothing else writes one. No `deleted_at`: there is no
    agent decision to take back the way there is for a `Memory`.
    """

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(index=True)
    provider: Mapped[str]
    #: The message it was split out of. Indexed, not a foreign key — this
    #: codebase declares none (see `task_id` on `Memory`) — because nothing
    #: here queries by it yet; kept for the operator to trace one back.
    source_message_id: Mapped[str] = mapped_column(index=True)
    content: Mapped[str]
    description: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, index=True)


class ExtractionMark(Base):
    """What node 0's last extraction for a task was made from, and what it came
    to. One row per task, rewritten whenever the reporter says something new.

    Keyed on `task_id` rather than given its own id: there is exactly one
    current answer per task, and a history of superseded fingerprints would be
    a log nobody reads.

    Not part of `dag_state`, though that is also one row per task. Node 0's
    result is deliberately excluded from the checkpoint — only its output
    decides whether the rest of what was checkpointed is still worth keeping —
    so writing this from inside node 0 would put back exactly what that
    exclusion takes out. This is a memo of node 0's *input*, which is a
    different thing with a different lifetime.
    """

    __tablename__ = "extraction_marks"

    task_id: Mapped[int] = mapped_column(primary_key=True)
    #: Over the extractor's whole per-call input — one `FullContext` since
    #: ticket 15 (D26), not a reconstruction naming some of its fields.
    #: `input_fingerprint` hashes what `would_ask` actually renders, so a
    #: fill (ticket 08's `known` shrinking the schema included) moves this
    #: the same way the transcript or the room does. See `input_fingerprint`.
    fingerprint: Mapped[str]
    #: The extractor's own output, so a skipped call applies the same fill
    #: rather than only saving the money.
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    #: The fields it asked about. An empty list means it asked nothing, which
    #: is a different thing from a null nobody wrote.
    clarify_fields: Mapped[list] = mapped_column(JSON, default=list)
    clarify_because: Mapped[str | None] = mapped_column(default=None)


class CompactionState(Base):
    """Whether node 0's own budget-based truncation is still worth trying
    for one task (board `what-the-room-already-knows`, ticket 08, D6).

    One row per task, created on the first pass that truncates and still
    ends up over budget. Two such passes and `Database.
    compaction_on_cooldown` starts answering `True`: the condition becomes
    visible to the operator (a warning names the task) rather than a check
    repeated on every pass for a build truncation cannot bring under budget
    — a single message larger than the budget, which dropping older
    messages can never fix.
    """

    __tablename__ = "compaction_state"

    task_id: Mapped[int] = mapped_column(primary_key=True)
    ineffective_count: Mapped[int] = mapped_column(default=0)


class ToolCall(Base):
    """One thing an agent reached for, and what came back.

    Its own table rather than a shape squeezed into `model_calls`: a tool call
    has no prompt and no tokens, and a model call has no arguments and no
    result. Sharing a table would mean half the columns null on every row and
    a discriminator nobody reads.
    """

    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent: Mapped[str]
    tool: Mapped[str] = mapped_column(index=True)
    arguments: Mapped[str]
    result: Mapped[str]
    #: A tool that fails here does not raise — the harness turns it into a
    #: message for the model — so without this a failure reads as an answer.
    failed: Mapped[bool] = mapped_column(default=False)
    latency_ms: Mapped[int | None] = mapped_column(default=None)
    message_id: Mapped[str | None] = mapped_column(index=True)
    task_id: Mapped[int | None] = mapped_column(index=True)
    node: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, index=True)


class AuditEntry(Base):
    """The append-only audit log (DESIGN-v2 §12).

    Application-level append-only: the kernel only inserts here — no update, no
    delete — so the log is a record of what happened, not a mutable view of the
    present. The database file itself is not tamper-proof, and a hash chain for
    that is deferred (§16, its trigger is a second principal or an auditor); this
    is the row store the chain would later cover.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    #: What happened, indexed because the readers ask by kind ("every approval",
    #: "the last grant for this server").
    event: Mapped[str] = mapped_column(index=True)
    #: Who did it — an operator name, or null for the system itself.
    actor: Mapped[str | None]
    #: The specifics: an approval's outbound id and payload hash, a grant's tool
    #: list, a plugin's tier. JSON so a new event kind carries its own fields
    #: without a column per kind.
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(IsoDateTime, index=True)
