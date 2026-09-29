"""Every memory type the store reads and writes, and its refusals. Over 200
lines because each structured kind carries its own schema here, and they
belong together."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from enum import StrEnum

from friday.sdk.memory import MemoryOrigin


class ModelMemoryKind(StrEnum):
    """The five kinds a model sees and writes — the enum the memory tools
    expose, unchanged by the memory kinds growing past them. A separate enum
    rather than a subset filtered at each call site, so the tools module has
    no name through which the other seven could reach a schema.
    `tests/test_memory_kinds.py` asserts no tool schema mentions them."""

    FACT = "fact"
    CONSTRAINT = "constraint"
    FINDING = "finding"
    DECISION = "decision"
    VOICE = "voice"


class MemoryRefused(ValueError):
    """A memory write the store will not make, and a sentence saying why —
    a `data` that does not fit its kind's schema (naming the field), a kind
    this origin may not write, or a natural key an active row already
    holds. `InstructionShaped` is the guard's own refusal and stays its own
    class."""


class MemoryKeyTaken(MemoryRefused):
    """The one refusal that is a conflict rather than a bad write: an active
    row of this kind already holds this natural key here. Its own class so
    the board can answer 409 without reading the sentence."""


# ---- one schema per structured kind (spec, "Memory: one store, twelve kinds")
#
# `data` is checked against these with `friday.kernel.harness.structured.fits` at
# `Database.memory_add` — the same checker the harness uses on a model's
# answer, so a wrong-typed field is refused with the field named. Every field
# a spec line marks optional (`?`) has a default; the rest are required.


@dataclass(frozen=True, slots=True)
class DecisionData:
    decided_on: str | None = None


@dataclass(frozen=True, slots=True)
class FindingData:
    task_id: int
    service: str
    confidence: float
    error_code: str | None = None
    refs: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SkillWhen:
    service: list[str] = field(default_factory=list)
    error_codes: list[str] = field(default_factory=list)
    path_patterns: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SkillData:
    when: SkillWhen


@dataclass(frozen=True, slots=True)
class PersonData:
    discord_id: str
    name: str
    role: str
    team: str


@dataclass(frozen=True, slots=True)
class RoomSummary:
    """What a summariser call may say about a room — the shape, once.

    Lives here rather than beside the summariser
    (`friday/kernel/memory/channel_context.py`, which re-exports it) because it is
    also the `summary` kind's schema (`SummaryData` adds the bookmark), and
    the store — which that module imports — has to check a row against it.

    **Four fields, where D9 named six**, and both absences are D2 applied to
    a prompt rather than to a table:

    `open_questions` is derived from the outbox with no model at all
    (`Database.unanswered_questions`, ticket 05). A model-written,
    channel-wide second version of the same thing could only ever disagree
    with the one that is a query over what was actually sent.

    `artifacts` waits for ticket 07, which is what produces one. Asking a
    model for the ids of things that do not exist yet is asking it to invent
    them.

    **This replaced a tuple of key names and a paragraph of prose that each
    described the same four fields.** `SUMMARY_FIELDS` was what the code
    enforced and `SUMMARY_JOB`'s indented block was what the model read, and
    two encodings of one contract drift in the direction nobody is looking:
    a fifth field added to the prose would have been invisible to the filter,
    and one added to the tuple invisible to the model. The prompt's own
    description is generated from this class now (`structured.describe`), and
    so is the validation (`Harness.run_structured`).

    Every field defaults to empty because a model that found nothing to say
    about `decisions` should say so by omission, and because a summary that
    fails to mention one field is still worth storing for the three it got.
    """

    topic: str = field(
        default="",
        metadata={"doc": "one line: what this room is for."},
    )
    facts: list[str] = field(
        default_factory=list,
        metadata={"doc": "what is true of this room and would still be true "
                         "next month — what a name refers to, which host is "
                         "which, where something lives. Copy a name exactly "
                         "as it is written."},
    )
    decisions: list[str] = field(
        default_factory=list,
        metadata={"doc": "what this room has settled and now works by."},
    )
    constraints: list[str] = field(
        default_factory=list,
        metadata={"doc": "what must not happen here, and what always has to."},
    )


@dataclass(frozen=True, slots=True)
class SummaryData(RoomSummary):
    """A `summary` row's `data`: the four fields the summariser answered, and
    the bookmark the YAML file kept in its own `state` section — which
    messages the summary was made from, under which version of the shape
    (board `read-it-the-way-the-operator-does`, ticket 10).

    A subclass rather than three more fields on `RoomSummary`, because that
    class is what the model is asked for, and a message id is not something
    to ask a model for. The renderer reads `RoomSummary`'s own fields by
    name and nothing else, so the bookmark is never rendered."""

    summary_from: str | None = None
    summary_of: str | None = None
    summary_version: int | None = None


class MemoryStatus(StrEnum):
    """Whether a memory is still current (D16).

    `ACTIVE` is a memory in force. `SUPERSEDED` is one a later memory
    replaced — the row survives with `superseded_by` naming its replacement,
    the same way `deleted_at`/`deleted_by` keep a retracted memory visible
    rather than gone. Every reader that serves a model reads `ACTIVE` rows
    only; a superseded or deleted one is for the operator's own view.
    """

    ACTIVE = "active"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class Memory:
    """Something an agent chose to remember, scoped to one channel.

    Replaced `remember`'s staging tier (ticket 09's D9): that wrote a guess
    nothing read back, promoted only once an approved outcome corroborated it,
    and had no producer for months. This is written and read back by the same
    kind of call, with the floor moved from an approval count to three
    narrower guarantees — scope, visibility, and never reaching a model except
    as a tool result. See `friday/kernel/toolsets/memory.py`.

    `id` is opaque and sparse rather than sequential, so a model that invents
    one fails instead of landing on a neighbouring row.

    `kind` decides who reads this row (`friday.kernel.memory.registry.readers_for`,
    D14). `status` and
    `superseded_by` are D16's lifecycle: correcting a memory's wording
    (`memory_update`) leaves it `ACTIVE` in place; replacing what it claims
    (`memory_supersede`) marks it `SUPERSEDED` and points `superseded_by` at
    the row that replaced it, rather than losing the old claim outright.

    `deleted_at`/`deleted_by` make a deletion visible rather than final: the
    row survives, so an operator asking "what did this used to say, and who
    took it out" has an answer. `memory_search`, `memory_update` and
    `memory_delete` all treat a deleted row as absent — the same "no such
    memory" a wrong-scope id gets, so a model cannot learn a row existed by
    the shape of the refusal.
    """

    id: str
    channel_id: str
    agent: str
    text: str
    kind: str
    created_at: datetime
    updated_at: datetime
    #: The message that produced this memory, when one is in scope.
    #: Nullable because the older memories were written before this link
    #: existed. The Rooms screen joins on it to mark the source row.
    source_message_id: str | None = None
    task_id: int | None = None
    deleted_at: datetime | None = None
    deleted_by: str | None = None
    status: str = MemoryStatus.ACTIVE
    superseded_by: str | None = None
    #: Who is answerable for the row (`MemoryOrigin`). Every row written
    #: before the column existed is a model's.
    origin: str = MemoryOrigin.MODEL
    #: A structured kind's natural key (`registry.natural_key`); `None` for prose.
    key: str | None = None
    #: A structured kind's payload, already checked against its `data` schema.
    data: dict[str, Any] | None = None


class CandidateStatus(StrEnum):
    """Where one candidate memory stands (board `what-the-room-already-knows`,
    ticket 12, D19, D20). `PENDING` is read by no prompt and no tool — that is
    the whole point, and the property `MemoryCandidate.status` exists to let a
    reader tell apart. `ACCEPTED`/`REJECTED` are both terminal and both stay
    visible: a rejected candidate is discarded, not deleted, so the operator
    can see what was proposed and turned down."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    """A memory an agent proposed, waiting for the operator's mark before it
    is anything more than that (board `what-the-room-already-knows`, ticket
    12, D19's second producer, D20).

    Never read by a prompt or a tool while `PENDING` — it lives in its own
    table for exactly that reason, the same guarantee `.scratch/what-the-room
    -already-knows/spec.md`'s D20 names: the tier that died was not wrong
    about the floor, only about having neither a producer nor a place for a
    person to look. This has both.

    `source_message_id` is what resolves it: the same message the operator
    already reacts to in order to confirm or reject the classification that
    opened this task (`Database.source_message_of`) — "the gesture that
    already confirms a classification" (D19), so there is one thing to learn,
    not two. `None` when no message was in scope when this was proposed,
    which leaves it resolvable by nothing — the same "silence is not a mark"
    a `PENDING` row with a message id can also end up in, just for a
    different reason.

    `memory_id` is set only once accepted, and only if the write actually
    landed — a channel at its cap (D18) or a line ticket 11's guard refused
    both leave it `None` with the candidate still marked `ACCEPTED`: the
    operator's judgement is recorded regardless of whether the row exists,
    the same way `Verdict` records what they said independent of what a
    later pass does with it.
    """

    id: str
    channel_id: str
    agent: str
    text: str
    kind: str
    task_id: int | None
    source_message_id: str | None
    status: str
    proposed_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    memory_id: str | None = None
