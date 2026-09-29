"""Shared store internals: the imports, row converters, id generators, selectors and
constants every repository module builds on.

Split out of `db.py` (ticket 16) so each repository mixin can reach them with one
`from friday.store._common import *`, leaving the method bodies moved out of the
god-class byte-for-byte unchanged. Internal to the store package.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import aliased
from sqlalchemy.pool import StaticPool

from friday.kernel.domain.conversation import ConversationId
from friday.kernel.domain.memory import (
    CandidateStatus,
    Memory,
    MemoryCandidate,
    MemoryKeyTaken,
    MemoryRefused,
    MemoryStatus,
)
from friday.kernel.domain.messages import Artifact, InboundEvent, MentionType
from friday.kernel.domain.monitor import (
    MessageFlow,
    ModelCall,
    MonitorEvent,
    MonitorSnapshot,
    ToolCall,
)
from friday.kernel.domain.outbound import (
    POLICY,
    AuditEntry,
    Outbound,
    payload_hash,
    payload_hash_of,
)
from friday.kernel.domain.state import FridayState
from friday.kernel.domain.states import (
    OPEN,
    IllegalTransition,
    OutboundState,
    TaskState,
    may_move,
)
from friday.kernel.domain.tasks import ExtractionMark, RunningTask, Task
from friday.kernel.harness.structured import fits

# The memory-kind machinery moved out of `domain` into its registry (ticket 12):
# the kind is a validated string now, and the store reaches up to the registry
# for a kind's writers, data schema, natural key and reader routing.
from friday.kernel.memory import registry as memory_kinds
from friday.kernel.ops.redact import scrub
from friday.kernel.text_transform import redact
from friday.sdk.memory import MemoryOrigin
from friday.store import schema

log = logging.getLogger(__name__)


def estimated_tokens(text: str) -> int:
    """Characters divided by four (D5) — an estimate, and the name says so.

    The configured provider is MiniMax, for which there is no tokenizer; a
    tokenizer for a different vendor would be confidently wrong rather than
    roughly right. Exported so `friday/kernel/dag/prepare.py` measures a budget's
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
OUTBOUND_DISPATCHING = OutboundState.DISPATCHING
OUTBOUND_SENT = OutboundState.SENT
OUTBOUND_DELIVERY_UNKNOWN = OutboundState.DELIVERY_UNKNOWN
OUTBOUND_FAILED = OutboundState.FAILED
OUTBOUND_SENT_MANUALLY = OutboundState.SENT_MANUALLY

#: Kinds the outbox refuses to select without an approval on the row. Kept as
#: data here because it is a `WHERE` clause; `friday.kernel.outbox.Kind` is where the
#: reasoning lives.
_NEEDS_APPROVAL = ("reply",)

#: The one kind that is a question to a reporter. Data here for the same
#: reason `_NEEDS_APPROVAL` is: it is a `WHERE` clause, and importing
#: `friday.kernel.outbox.Kind` would put the store below a module that reads it.
_ASK = "ask_for_details"


#: How long a write waits for a competing lock before giving up (ticket 04).
#: A read from the board holds the database only for the length of the read;
#: without this a write that lands during one raises `database is locked` at
#: once, and with it the write simply waits the read out. Three seconds is far
#: longer than any read here takes and short enough not to hang the agent.
#:
#: Set explicitly rather than left to the driver: aiosqlite happens to default
#: to 5000ms today, but a value a safety property depends on is one this
#: process names, not one it inherits and hopes stays put.
BUSY_TIMEOUT_MS = 3000


def _now() -> datetime:
    return datetime.now(UTC)


#: `[artifact <id>: <what it is>]`, as `_record_artifacts` writes it.
_ARTIFACT_REF = re.compile(r"\[artifact ([0-9a-f]+): [^\]]*\]")


def _with_artifact_ids(
    redacted: str | None, original: str, held: dict[str, str]
) -> str:
    """One message's text with each verbatim span named by its artifact id
    and then shown.

    Built from the reference `_record_artifacts` already wrote rather than by
    re-splitting the text: re-splitting is what that method's own docstring
    warns can disagree with the first split, and a disagreement here would
    put a span under the wrong id.

    A message with no `redacted_text` — no code, or recorded before that
    column existed — is its own text, unchanged and unnamed. A reference
    whose artifact has gone is left as it is: a name for something nobody can
    read is still better than a silent gap where a curl used to be.
    """
    if not redacted:
        return original
    return _ARTIFACT_REF.sub(
        lambda found: (
            f"{found.group(0)}\n{held[found.group(1)]}"
            if found.group(1) in held
            else found.group(0)
        ),
        redacted,
    )


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
    data: dict[str, Any] | None, keys: Mapping[str, str], text: str
) -> bool:
    when = (data or {}).get("when") or {}
    said = text.lower()
    return bool(
        any(value and value in when.get(name, []) for name, value in keys.items())
        or any(k.lower() in said for k in when.get("keywords", []) if k)
    )


def _checked_data(
    kind: str, data: dict[str, Any] | None, key: str | None
) -> tuple[dict[str, Any] | None, str | None]:
    """`data` checked against `kind`'s schema, and the natural key read off
    it — or `MemoryRefused` naming what did not fit. What is stored is the
    validated instance turned back into plain JSON, so an unknown key the
    checker dropped is not kept either."""
    schema_type = memory_kinds.data_of(kind)
    if schema_type is None:
        if data:
            raise MemoryRefused(f"a {kind} is prose — it carries no data")
        return None, None
    if data is None and kind == memory_kinds.DECISION:
        return None, None
    if not isinstance(data, dict):
        raise MemoryRefused(f"a {kind} needs its data as an object")
    fitted, unfit = fits(data, schema_type)
    if unfit is not None:
        raise MemoryRefused(f"{kind} data does not fit: {unfit.why}")
    stored = asdict(fitted)
    return stored, memory_kinds.natural_key(kind, stored, key)


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
        approved_payload_hash=row.approved_payload_hash,
    )


def _audit(row: schema.AuditEntry) -> AuditEntry:
    return AuditEntry(
        id=row.id,
        at=row.at,
        event=row.event,
        actor=row.actor,
        detail=dict(row.detail or {}),
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


__all__ = [
    "BUSY_TIMEOUT_MS",
    "OPEN",
    "OUTBOUND_DELIVERY_UNKNOWN",
    "OUTBOUND_DISPATCHING",
    "OUTBOUND_FAILED",
    "OUTBOUND_QUEUED",
    "OUTBOUND_SENT",
    "OUTBOUND_SENT_MANUALLY",
    "POLICY",
    "_ARTIFACT_REF",
    "_ASK",
    "_NEEDS_APPROVAL",
    "_NEWEST_FIRST",
    "_OLDEST_FIRST",
    "_SQL_KEYWORDS",
    "Any",
    "Artifact",
    "AsyncSession",
    "AuditEntry",
    "CandidateStatus",
    "ConversationId",
    "ExtractionMark",
    "FridayState",
    "IllegalTransition",
    "InboundEvent",
    "Integer",
    "IntegrityError",
    "Mapping",
    "Memory",
    "MemoryCandidate",
    "MemoryKeyTaken",
    "MemoryOrigin",
    "MemoryRefused",
    "MemoryStatus",
    "MentionType",
    "MessageFlow",
    "ModelCall",
    "MonitorEvent",
    "MonitorSnapshot",
    "Outbound",
    "OutboundState",
    "RunningTask",
    "StaticPool",
    "Task",
    "TaskState",
    "ToolCall",
    "_artifact",
    "_artifact_id",
    "_audit",
    "_balanced",
    "_candidate",
    "_checked_data",
    "_describe_artifact",
    "_event",
    "_flush_keyed",
    "_memory",
    "_memory_id",
    "_model_call",
    "_now",
    "_outbound",
    "_runbook_matches",
    "_task",
    "_tool_call",
    "_with_artifact_ids",
    "aliased",
    "asdict",
    "async_sessionmaker",
    "asyncio",
    "cast",
    "create_async_engine",
    "datetime",
    "delete",
    "estimated_tokens",
    "event",
    "fits",
    "func",
    "insert",
    "literal",
    "log",
    "logging",
    "may_move",
    "memory_kinds",
    "or_",
    "payload_hash",
    "payload_hash_of",
    "re",
    "redact",
    "replace",
    "schema",
    "scrub",
    "secrets",
    "select",
    "timedelta",
    "timezone",
    "update",
]
