from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from enum import StrEnum

from friday.domain.conversation import ConversationId, resolve
from friday.domain.validation import InSet, Matches, OneOf


class MentionType(StrEnum):
    """How the watched account was addressed."""

    DIRECT = "direct"
    ROLE = "role"
    DM = "dm"


@dataclass(frozen=True, slots=True)
class InboundEvent:
    """A message addressed to the watched account, normalised by a provider.

    `mention_type` is None when the provider saw the message but the watched
    account was not addressed in it.
    """

    provider: str
    provider_message_id: str
    channel_id: str
    thread_id: str | None
    author_id: str
    author_name: str
    text: str
    created_at: datetime
    mention_type: MentionType | None
    #: Written by the watched account. Reported, not interpreted: whether it
    #: means "ignore" is the inbox's decision, and the responder needs these as
    #: examples of how the operator actually writes.
    is_own: bool = False
    #: The `provider_message_id` this replies to, if it is a reply. One of the
    #: three structural signals a relevant-context filter reads — the other two
    #: are `mention_type` and `is_own` itself.
    reply_to: str | None = None
    #: Code the message carried, verbatim: a curl, a stack trace, a payload.
    #: Already inside `text` too — this is the same content addressable as
    #: itself, for anything that wants the code without the prose around it.
    code: tuple[str, ...] = ()
    #: Files posted with the message. Named, never fetched.
    attachments: tuple = ()

    @property
    def conversation(self) -> ConversationId:
        """Where the exchange is happening. Delegated, never derived here —
        the rules live in one module so a platform that threads differently can
        be added without every caller learning about it."""
        return resolve(self)


@dataclass(frozen=True, slots=True)
class Task:
    """A piece of work derived from a mention.

    `state` is a plain string here; the legal transitions between states are
    ticket 05's concern. Triage only ever creates a task in one of two: ready to
    work on, or waiting on a human.
    """

    id: int
    conversation: ConversationId
    type: str
    state: str
    confidence: float
    params: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Outbound:
    """Something to send, held as data rather than performed as a call.

    `kind` decides whether it needs approval; `sender` decides which identity
    says it. Approval itself is a fact about the task, not about this row.
    """

    id: int
    task_id: int | None
    conversation: ConversationId
    kind: str
    sender: str
    text: str
    reply_to: str | None = None
    state: str = "queued"
    attempts: int = 0
    last_error: str | None = None


# ---- task parameters -------------------------------------------------

"""What each kind of task carries.

Here rather than in `friday.triage`, which is where they were: a workflow
reached for the schema of a task's parameters *through the agent that happens
to fill them in*. The types describe the work, not the thing that recognised
it — and their annotations are read directly to decide what a task cannot
proceed without.
"""

TaskType = Literal["api_issue", "access_request", "doc_question", "skip"]


@dataclass(frozen=True, slots=True)
class ApiIssueParams:
    #: The docstring below reaches a model: triage's `classify` tool reads
    #: it as this type's description, one line each, the same place its
    #: fields are defined. `AccessRequestParams` and `DocQuestionParams`
    #: carry the same pattern.
    """An API is behaving incorrectly: an error, a wrong response, a failure."""

    #: Every field has a default, because nothing fills them in at
    #: construction time any more. Triage classifies and stops; the task is
    #: opened with no parameters at all, and the extractor fills them from
    #: what the reporter actually wrote.
    #:
    #: `doc` is the field's meaning, *for the extraction model*. It renders
    #: into the extractor's prompt, so it is written to the model: what to
    #: look for, what shape it has, and that null beats a guess. These lines
    #: lived in triage's tool docstrings until triage stopped extracting —
    #: removing them then, instead of moving them here, left the extractor
    #: reading a schema of "summary: summary". Same place as the field, so a
    #: field and its meaning cannot drift apart again.
    summary: str = field(
        default="",
        metadata={
            "doc": "One line saying what is wrong, in Vietnamese, in your own "
            "words. The only field you write rather than copy."
        },
    )
    environment: str | None = field(
        default=None,
        metadata={
            "doc": "Which environment they named: production, staging or dev. "
            "'prod' is production, 'stg' is staging. null if none is named."
        },
    )
    correlation_id: str | None = field(
        default=None,
        metadata={
            "doc": "The correlation id, trace id, request id or x-request-id "
            "in what they wrote, copied exactly — it is matched by machine. "
            "Usually shaped like a uuid. null if absent."
        },
    )
    curl: str | None = field(
        default=None,
        metadata={
            "doc": "The curl command or raw request they included, verbatim "
            "with its line breaks — somebody will paste it into a terminal. "
            "null if absent."
        },
    )

    #: Validate catches what the LLM extractor got wrong. `environment` has to
    #: be one of the three environments we actually serve; `correlation_id`
    #: has to look like a uuid for Loki's query_range filter to find it.
    #:
    #: `_traceable` is this type's override of the general required-ness rule,
    #: which reads its answer off the annotations: all three of `environment`,
    #: `correlation_id` and `curl` are `str | None`, so none of them is
    #: individually required — and yet a report with none of them cannot be
    #: investigated at all. An id *or* a curl makes a request findable; that is
    #: not something a type can say, which is what `OneOf` is for.
    #:
    #: Without it nothing in the gate knew, so a report with nothing to trace
    #: on validated cleanly and the graph ran its whole path to discover it
    #: could do nothing.
    _RULES = {
        "environment": InSet(frozenset({"production", "staging", "dev"})),
        "correlation_id": Matches(
            r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$",
            name="uuid",
        ),
        "_traceable": OneOf(fields=("correlation_id", "curl")),
    }


@dataclass(frozen=True, slots=True)
class AccessRequestParams:
    """Someone is asking for permission or access to a project or repository."""

    project: str = field(
        default="",
        metadata={
            "doc": "The project, repository or system they want access to, "
            "named as they named it. Empty if they did not say."
        },
    )
    permission: str = field(
        default="",
        metadata={
            "doc": "What kind of access: read, write, admin, or their own "
            "words for it. Empty if they did not say."
        },
    )
    summary: str = field(
        default="",
        metadata={
            "doc": "One line saying who wants what, in Vietnamese, in your "
            "own words."
        },
    )


@dataclass(frozen=True, slots=True)
class DocQuestionParams:
    """A question about documentation, a specification, or intended behaviour."""

    question: str = field(
        default="",
        metadata={
            "doc": "What they want to know, kept close to their own phrasing "
            "— rewording a question changes it."
        },
    )
    doc_ref: str | None = field(
        default=None,
        metadata={
            "doc": "The document, spec or page they referred to, if they "
            "named one. null if none."
        },
    )


#: Note there is no `SkipParams`. A skip opens no task, so it has no
#: parameters to carry — triage says `skip` and the message is recorded as
#: having been looked at. There was one, holding a `reason`, until triage
#: stopped producing anything but a type and a confidence.
Params = ApiIssueParams | AccessRequestParams | DocQuestionParams

#: Task type -> its parameters dataclass. Every classifiable type is here;
#: `register_dags` builds a graph for each entry and `create_task`'s tool
#: schema is generated from it. `skip` is deliberately absent — see above.
PARAMS: dict[str, type] = {
    "api_issue": ApiIssueParams,
    "access_request": AccessRequestParams,
    "doc_question": DocQuestionParams,
}

#: Written by the model about the message, not supplied by the person who
#: sent it. Asking someone for a summary of their own message is nonsense.
MODEL_AUTHORED = frozenset({"summary"})


@dataclass(frozen=True, slots=True)
class Memory:
    """Something an agent chose to remember, scoped to one channel.

    Replaced `remember`'s staging tier (ticket 09's D9): that wrote a guess
    nothing read back, promoted only once an approved outcome corroborated it,
    and had no producer for months. This is written and read back by the same
    kind of call, with the floor moved from an approval count to three
    narrower guarantees — scope, visibility, and never reaching a model except
    as a tool result. See `friday/tools/memory.py`.

    `id` is opaque and sparse rather than sequential, so a model that invents
    one fails instead of landing on a neighbouring row.

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
    task_id: int | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    deleted_by: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryScope:
    """Where a memory belongs and who wrote it. Runtime-supplied, every field.

    Here rather than beside the tools that use it (`friday/tools/memory.py`):
    it is also part of `Database`'s own signature — five `friday/store/db.py`
    methods take a `scope`, and a store may not import from `friday/tools/`.
    It was a quoted forward reference there before this move
    (`scope: "MemoryScope"  # type: ignore[name-defined]`), which is a real
    gap and not a stylistic one: mypy checked nothing about the one parameter
    whose entire job is "never wrong". `friday/domain/` is the vocabulary
    both sides read, which is what a store and a tool factory are allowed to
    share.

    `channel_id` is the read *and* write boundary — a memory written in one
    room is invisible in another. Not a nicety: this system's rooms are
    different teams, and a fact learned in one is a leak in the next.

    `task_id` and `agent` are provenance. They are never searched on; they are
    what lets the operator's board answer "who wrote this, and while doing
    what" about a line the agent is now acting on. **Both are required, and
    `task_id` is nullable rather than optional** — a run that belongs to no
    task says so by passing `None`, which is a different statement from
    forgetting to pass it. They had defaults, under a docstring that said
    every field was runtime-supplied; a default is how provenance goes missing
    without anybody deciding it should.

    **This is the run's context object, not a closure variable**, and the
    distinction is the difference between working and being silently wrong. An
    agent here is built once, at startup — `Responder.build` in the composition
    root, reused for every task in every channel — so a scope captured when the
    tools were made would pin every room's memory to whichever room happened to
    be first. It arrives per call instead, as `Harness.run(context=...)`, the
    way `ClarifyCapture` and `FieldsCapture` already do. The model still cannot
    name it, which was the point.
    """

    channel_id: str
    task_id: int | None
    agent: str


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One thing an agent reached for, and what came back.

    Beside `ModelCall` rather than inside it: a prompt says what an agent was
    *asked*, and says nothing about what it did. Which of the four skill tools
    an agent actually reaches for — the catalogue by name, or the search when
    the catalogue's wording did not surface it — was a question nothing could
    answer, and it becomes an expensive one the day a tool leaves this process
    with arguments a model chose.

    `failed` rather than an error string, because a tool that fails here does
    not raise: `harness._tool_failed` turns it into a message for the model,
    which is a *result* as far as the SDK is concerned. Without this flag a
    failure is indistinguishable from an answer that happens to read like one.
    """

    agent: str
    tool: str
    arguments: str
    result: str
    failed: bool = False
    latency_ms: int | None = None
    message_id: str | None = None
    task_id: int | None = None
    node: str | None = None
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass(frozen=True, slots=True)
class ModelCall:
    """Both sides of one model call.

    Kept because "why did it classify that as an access request?" is always
    asked after the fact, and a log line answers it while the process is alive
    and never again.
    """

    agent: str
    model: str
    system_prompt: str
    prompt: str
    output: str
    input_tokens: int
    output_tokens: int
    #: What the call was about, and none of these is the same question.
    #:
    #: `message_id` suits triage — one call, one message — and nothing
    #: downstream: an extractor runs on every pass of a task's graph against
    #: many messages, and a responder answers a task. `node` says which step of
    #: a graph asked, where a graph asked at all. All three are absent for the
    #: summariser, which belongs to a channel rather than to any of them.
    message_id: str | None = None
    task_id: int | None = None
    node: str | None = None
    #: How long the provider took, wall clock. The one number here that is
    #: measured rather than passed in.
    latency_ms: int | None = None
    #: Which attempt of its run this was, 1-based. An ordinal, not a total:
    #: one row is one call to the provider, and the provider bills per call —
    #: so a run rate-limited once leaves two rows, each with its own prompt
    #: and its own cost, rather than one row claiming to be two.
    attempt: int = 1
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass(frozen=True, slots=True)
class MessageFlow:
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

    The spine is a message and not a task, which is the whole of D5 on
    `.scratch/a-window-on-the-whole-path/`: when the classifier runs there is
    no task yet and its call correlates by `message_id` alone, so a
    task-spined view loses triage entirely — and it loses every `skip`, which
    is the outcome an operator most wants to interrogate.

    Assembled in one store call rather than joined in a browser (D6). Four
    requests read four instants of a database being written to, and the path
    they render is one that never existed.

    `decision` is the triage verdict as `mark_triaged` stored it — `type`,
    `confidence`, `params` — and `None` means nothing has looked at this
    message yet, which is a state and not an absence. A message the
    sensitive-word prefilter held has a decision and no `model_calls` at all;
    the word that held it is in `params["reason"]`, and without that the path
    stops with nothing to explain it.

    `model_calls` merges the calls correlated by message with the ones
    correlated by task and orders the result by time, because they are one
    sequence — triage, then extraction — and handing a reader two lists to
    interleave is handing them the join this class exists to do.
    """

    message: InboundEvent
    #: Everything the same person went on to say. Triage reads the turn, not
    #: the message, so a path showing only the mention shows less than what
    #: was actually classified.
    turn: list[InboundEvent]
    decision: dict[str, Any] | None
    triaged_at: datetime | None
    task: Task | None
    model_calls: list[ModelCall]
    tool_calls: list[ToolCall]
    outbound: list[Outbound]
