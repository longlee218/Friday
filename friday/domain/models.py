from __future__ import annotations

from dataclasses import dataclass, field, replace
from dataclasses import fields as dataclass_fields
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
    #: Set by the store when the message opened a task (ticket 11).
    #: `None` is the ordinary case; a value means the operator's Rooms
    #: screen should mark this message with a task glyph.
    task_id: int | None = None
    #: Set by the store when an agent wrote a memory while processing this
    #: message (ticket 11). The Rooms screen marks these with an
    #: enrichment glyph so the operator can find what the agent decided
    #: was worth remembering.
    is_enrichment: bool = False
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
    #: Most recent activity on this task — the latest model_call or
    #: tool_call that ran for it. `None` when nothing has run yet
    #: (an empty plan). The Monitor screen reads this to show "5s
    #: ago", "2m ago"; the BoardScreen reads it to sort cards
    #: newest-first within a column.
    last_activity_at: datetime | None = None
    #: Number of agent attempts. `0` for tasks that never had a
    #: model call. The Monitor screen renders this as a small
    #: badge so a stuck task is distinguishable from a finished
    #: one at a glance.
    attempts: int = 0


@dataclass(frozen=True, slots=True)
class Outbound:
    """Something to send, held as data rather than performed as a call.

    `kind` decides whether it needs approval; `sender` decides which identity
    says it. Approval itself is a fact about this row, not about its task —
    approving one reply must not approve the next.
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
    #: On an approval card, the id of the row it asks about.
    approves: int | None = None


# ---- task parameters -------------------------------------------------

"""What each kind of task carries.

Here rather than in `friday.triage`, which is where they were: a workflow
reached for the schema of a task's parameters *through the agent that happens
to fill them in*. The types describe the work, not the thing that recognised
it — and their annotations are read directly to decide what a task cannot
proceed without.
"""



@dataclass(frozen=True, slots=True)
class ApiIssueParams:
    #: The docstring below reaches a model: triage's `classify` tool reads
    #: it as this type's description, one line each, the same place its
    #: fields are defined. `AccessRequestParams` and `DocQuestionParams`
    #: carry the same pattern.
    """Something this team's systems did, or did not do, that somebody wants
    looked at: an integration failing, a request, log, curl or response with
    an error code to check, a symptom with no name yet ("I bought the plan
    at 15:00 and half an hour later the coins are still not there"), or a
    question about what an endpoint is for, which one fits their case, and
    how its rules behave — an API is business logic reachable over HTTP, so
    a question about that logic belongs here rather than in
    doc_question."""

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
    #:
    #: `ask` is how to ask a *person* about it — the same field, a different
    #: reader, so a different string. `doc` addresses a model about
    #: recognising a value ("null if none is named"); `ask` is the phrase the
    #: responder writes a question from, and what a reporter eventually reads
    #: is the responder's wording of it, never this text verbatim.
    #:
    #: It lived in a dict in the node that renders the question until ticket
    #: 13 — the arrangement the paragraph above exists to describe the failure
    #: of — and had already drifted: `project` was askable with no entry, and a
    #: fallback made it read acceptably enough that nothing said so. Every
    #: askable field carries one now (`askable_fields` below), and a test says
    #: so. `OneOf` holds the argument for where a *rule*'s phrase lives.
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
            "'prod' is production, 'stg' is staging. null if none is named.",
            "ask": "which environment you're on",
        },
    )
    correlation_id: str | None = field(
        default=None,
        metadata={
            "doc": "The correlation id, trace id, request id or x-request-id "
            "in what they wrote, copied exactly — it is matched by machine. "
            "Usually shaped like a uuid. null if absent.",
            "ask": "the correlationId",
        },
    )
    curl: str | None = field(
        default=None,
        metadata={
            # Board `read-it-the-way-the-operator-does`, ticket 18. This used
            # to say "verbatim with its line breaks — somebody will paste it
            # into a terminal", and that is still what the field has to hold;
            # it is no longer what the model is asked to produce. Task 6
            # stored 676 characters of a 678-character Bearer token because
            # copying it out by hand is a thing a model does imperfectly and
            # a thing code does not do at all.
            "doc": "The id of the artifact holding the request they pasted — "
            "the `ab12cd34` in `[artifact ab12cd34: …]`, on its own, nothing "
            "else. Do not copy the request itself: it is put back for you. "
            "If they typed it inline with no artifact around it, give the "
            "command as they wrote it. null if there is no request at all.",
            "ask": "the curl you used",
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
        "_traceable": OneOf(
            fields=("correlation_id", "curl"),
            ask="the correlationId, or the curl you used",
        ),
    }


@dataclass(frozen=True, slots=True)
class AccessRequestParams:
    """Someone wants to be let in somewhere: a repository, an environment, a
    dashboard, a channel, an API key, a role, a permission — for themselves
    or for somebody joining. Asked outright ("can I get write access to the
    payments repo") or told as a complaint ("I cannot open the staging
    repo"); either way what unblocks them is being granted something, not
    something being fixed."""

    project: str = field(
        default="",
        metadata={
            "doc": "The project, repository or system they want access to, "
            "named as they named it. Empty if they did not say.",
            "ask": "which project you need access to",
        },
    )
    permission: str = field(
        default="",
        metadata={
            "doc": "What kind of access: read, write, admin, or their own "
            "words for it. Empty if they did not say.",
            "ask": "what access you need",
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
    """Someone asks where something is written down, or what a document,
    spec or runbook says: the answer is a pointer to writing, or a line out
    of it. They have not run anything and are reporting no behaviour. If
    they tried something and it did not do what they expected, or they ask
    what an endpoint is for and how its rules work, that is api_issue."""

    question: str = field(
        default="",
        metadata={
            "doc": "What they want to know, kept close to their own phrasing "
            "— rewording a question changes it.",
            "ask": "what you would like to know",
        },
    )
    doc_ref: str | None = field(
        default=None,
        metadata={
            "doc": "The document, spec or page they referred to, if they "
            "named one. null if none.",
            "ask": "which document you mean",
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

#: The one decision that opens no work. Everything else in `DECISIONS` names
#: a kind of task; this names the absence of one.
SKIP = "skip"

#: **The closed set of things triage may conclude** — every type that can open
#: a task, plus `SKIP`. Derived from `PARAMS`, so a fourth task type is a
#: fourth `Params` class and nothing else.
#:
#: Written down once because it was written down three times, and in three
#: shapes that could disagree: a `TaskType` literal here that no annotation
#: ever used, a `CLASSIFIABLE` tuple in the store, and the `classify`/`skip`
#: tool pair that only added up to this set if you knew about both. The day a
#: fourth type is registered, two of those three would have gone on describing
#: a three-type system with nothing failing. Board `every-answer-has-a-shape`,
#: ticket 02; D6 builds the model's own enum from it.
DECISIONS: tuple[str, ...] = (*PARAMS, SKIP)

#: Written by the model about the message, not supplied by the person who
#: sent it. Asking someone for a summary of their own message is nonsense.
MODEL_AUTHORED = frozenset({"summary"})


def askable_fields(params_cls: type) -> tuple[str, ...]:
    """The fields of one type a reporter can be asked about.

    One definition, because two disagreed. The closed set an extractor may
    ask about was built from `__dataclass_fields__`, and ticket 13's guard
    checked
    `dataclasses.fields()` — the same set today and not the same set in
    general: `__dataclass_fields__` keeps `ClassVar` and `InitVar` entries as
    pseudo-fields, so annotating `_RULES` as a `ClassVar` would have offered
    the model `_RULES` as a field to ask about while the guard skipped it.
    Whatever askable means, the tool and the guard now mean the same thing.

    `dataclasses.fields()` is the filter that drops the pseudo-fields, and
    `MODEL_AUTHORED` drops what the model writes rather than reads.
    """
    from dataclasses import fields as _dataclass_fields

    return tuple(
        f.name for f in _dataclass_fields(params_cls) if f.name not in MODEL_AUTHORED
    )


class MemoryKind(StrEnum):
    """What kind of thing a memory is — thirteen, closed (board
    `read-it-the-way-the-operator-does`, spec "Memory: one store, twelve
    kinds", widening D14's five).

    The first five are prose a model reads and writes. The rest were a YAML
    file and an operator's head: `runbook` (how the operator reasons about
    one kind of fault, in words), `summary` (one per room, replacing
    `derived`), and six structured kinds that never reach a model at all and
    only parameterise code — `project`, `service`, `route`, `dependency`,
    `person`, `environment`.

    **`environment` is the thirteenth, and it amends D1** ("environment from
    the domain, by rule, in code"), on the operator's call of 2026-09-21: a
    rule in code that names `aperogroup.ai` is an installation written into a
    module, and Friday is meant to serve more rooms than one company's. It is
    also a rule this company's own domains already break — the 2026-09-18
    survey found `api-mobile-spec-reviewer.aperogroup.ai` served from `dev`
    with no `.dev` in it, and `payment-service` on two domains resolving to
    different endpoints. A longest-suffix table holds both the rule and its
    exceptions without a branch for either.

    **The reader is a function of the kind, not a second column** — see
    `readers_for`. `preference` was considered and rejected: in this domain
    every preference is either `VOICE` or `CONSTRAINT`, and a value that
    cannot be told apart from its neighbours is one a model will place at
    random — which is also why a model is offered `ModelMemoryKind`, not
    this.
    """

    FACT = "fact"
    CONSTRAINT = "constraint"
    FINDING = "finding"
    DECISION = "decision"
    VOICE = "voice"
    RUNBOOK = "runbook"
    SUMMARY = "summary"
    PROJECT = "project"
    SERVICE = "service"
    ROUTE = "route"
    DEPENDENCY = "dependency"
    PERSON = "person"
    ENVIRONMENT = "environment"


class ModelMemoryKind(StrEnum):
    """The five kinds a model sees and writes — the enum the memory tools
    expose, unchanged by `MemoryKind` growing to twelve. A separate enum
    rather than a subset filtered at each call site, so the tools module has
    no name through which the other seven could reach a schema.
    `tests/test_memory_kinds.py` asserts no tool schema mentions them."""

    FACT = "fact"
    CONSTRAINT = "constraint"
    FINDING = "finding"
    DECISION = "decision"
    VOICE = "voice"


class MemoryOrigin(StrEnum):
    """Who is answerable for a memory row. `ADMIN` is the operator, through
    the board's own routes; a model-origin call may not update, supersede or
    delete an `ADMIN` row, and is told "no such memory" rather than why."""

    MODEL = "model"
    ADMIN = "admin"


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


_READERS: dict[MemoryKind, frozenset[str]] = {
    MemoryKind.FACT: frozenset({"extractor", "diagnose"}),
    MemoryKind.CONSTRAINT: frozenset({"extractor", "diagnose"}),
    MemoryKind.DECISION: frozenset({"extractor", "diagnose"}),
    MemoryKind.FINDING: frozenset({"diagnose", "extractor"}),
    MemoryKind.VOICE: frozenset({"responder"}),
    MemoryKind.RUNBOOK: frozenset({"diagnose"}),
    MemoryKind.SUMMARY: frozenset({"triage", "responder"}),
    MemoryKind.PROJECT: frozenset({"code"}),
    MemoryKind.SERVICE: frozenset({"code"}),
    MemoryKind.ROUTE: frozenset({"code"}),
    MemoryKind.ENVIRONMENT: frozenset({"code"}),
    MemoryKind.DEPENDENCY: frozenset({"code"}),
    MemoryKind.PERSON: frozenset({"code"}),
}


def readers_for(kind: str) -> frozenset[str]:
    """Who reads a memory of this kind — agents by name, or `"code"` for a
    structured kind only a tool call is parameterised by. A set, because
    `finding` has two readers. Derived from `kind` rather than stored beside
    it, so nothing has to keep two fields in agreement (D14). Raises on a
    kind outside `MemoryKind`, the way a wrong `TaskState` string would."""
    return _READERS[MemoryKind(kind)]


_WRITERS: dict[MemoryKind, frozenset[MemoryOrigin]] = {
    MemoryKind.FACT: frozenset(MemoryOrigin),
    MemoryKind.CONSTRAINT: frozenset(MemoryOrigin),
    MemoryKind.DECISION: frozenset(MemoryOrigin),
    MemoryKind.VOICE: frozenset(MemoryOrigin),
    MemoryKind.FINDING: frozenset({MemoryOrigin.MODEL}),
    MemoryKind.SUMMARY: frozenset({MemoryOrigin.MODEL}),
    MemoryKind.RUNBOOK: frozenset({MemoryOrigin.ADMIN}),
    MemoryKind.PROJECT: frozenset({MemoryOrigin.ADMIN}),
    MemoryKind.SERVICE: frozenset({MemoryOrigin.ADMIN}),
    MemoryKind.ROUTE: frozenset({MemoryOrigin.ADMIN}),
    MemoryKind.ENVIRONMENT: frozenset({MemoryOrigin.ADMIN}),
    MemoryKind.DEPENDENCY: frozenset({MemoryOrigin.ADMIN}),
    MemoryKind.PERSON: frozenset({MemoryOrigin.ADMIN}),
}


def writers_for(kind: str) -> frozenset[MemoryOrigin]:
    """Which origin may write a memory of this kind — the spec table's
    "Written by" column. `Database.memory_add` refuses anything else."""
    return _WRITERS[MemoryKind(kind)]


#: The four kinds the extractor reads. `VOICE` is the responder's alone.
#: **Derived from `readers_for`, not a second enumeration beside it** — a
#: `MemoryKind` this misses only if `readers_for` itself would misroute it,
#: rather than a hand-kept list that could drift from what `readers_for`
#: actually decides (found in code review: an earlier version of this line
#: listed the four kinds by hand, which is exactly the "two fields that have
#: to agree" D14 exists to rule out).
DOMAIN_KINDS = frozenset(k for k in MemoryKind if "extractor" in readers_for(k))


# ---- one schema per structured kind (spec, "Memory: one store, twelve kinds")
#
# `data` is checked against these with `friday.agent.structured.fits` at
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
class RunbookWhen:
    services: list[str] = field(default_factory=list)
    error_codes: list[str] = field(default_factory=list)
    path_patterns: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RunbookData:
    when: RunbookWhen


@dataclass(frozen=True, slots=True)
class ProjectData:
    name: str
    #: **Picked, not spelled** (ticket 19). A path on the operator's own
    #: machine whose typo reads exactly like a correct one: the row looks
    #: right in the form and `ReadFailingCode` quietly reads nothing.
    #: Declared here for the same reason `names` is — the form has no list
    #: of its own to disagree with.
    repo_path: str = field(metadata={"picks": "directory"})
    default_branch: str
    stack: str
    docs_paths: list[str] = field(default_factory=list)
    error_codes_doc: str | None = None


@dataclass(frozen=True, slots=True)
class ProdPlacement:
    cluster: str
    namespace: str
    app: str


@dataclass(frozen=True, slots=True)
class DevPlacement:
    kube_context: str
    namespace: str
    pod_pattern: str


@dataclass(frozen=True, slots=True)
class ServiceData:
    name: str
    #: **`names` is a foreign key, declared where the field is.** The store
    #: matches this against a `project` row's own key by string equality, and
    #: for as long as that was a fact only two call sites knew, the form
    #: asked for it as free text — a question whose wrong answers look
    #: exactly like its right ones, and the first six rows ever typed proved
    #: it (ticket 19). Declared here so the form offers the rows that exist
    #: and a later check can refuse one that does not, from one statement
    #: rather than two that have to agree.
    project: str = field(metadata={"names": "project"})
    prod: ProdPlacement
    dev: DevPlacement


@dataclass(frozen=True, slots=True)
class EnvironmentData:
    """What a domain suffix means: ours, and which environment.

    **Longest suffix wins**, which is what lets one table hold a rule and its
    exceptions with no branch for either. `aperogroup.ai → production` and
    `dev.aperogroup.ai → dev` are the rule; one row for
    `api-mobile-spec-reviewer.aperogroup.ai → dev` is an exception, and it
    wins by being longer rather than by being special.

    A domain no row matches is **external** — not ours, and the graph ends
    there promising nothing. A room with no rows at all knows nothing about
    any domain, which is a different thing and says so.
    """

    suffix: str
    env: Literal["dev", "production"]


@dataclass(frozen=True, slots=True)
class RouteData:
    #: The exact host. `environment` answers "ours, and which" for a family
    #: of hosts; this answers "which service" for one. `env` is carried here
    #: too, and the redundancy is deliberate: these are two rows an operator
    #: types by hand, and `Resolve` refuses when they disagree rather than
    #: picking — a production search run against dev is not a thing to
    #: discover from its results.
    domain: str
    env: Literal["dev", "production"]
    #: The same foreign key as `ServiceData.project`, and the same reason.
    service: str = field(metadata={"names": "service"})


@dataclass(frozen=True, slots=True)
class DbCheck:
    table: str
    key_column: str
    state_column: str


@dataclass(frozen=True, slots=True)
class DependencyData:
    from_service: str
    to_service: str
    via: Literal["http", "queue", "webhook"]
    join_key: str
    db_checks: list[DbCheck] = field(default_factory=list)


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
    (`friday/memory/channel_context.py`, which re-exports it) because it is
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


#: Each kind's `data` schema; `None` is a prose kind, which carries no `data`.
MEMORY_DATA: dict[MemoryKind, type | None] = {
    MemoryKind.FACT: None,
    MemoryKind.CONSTRAINT: None,
    MemoryKind.VOICE: None,
    MemoryKind.DECISION: DecisionData,
    MemoryKind.FINDING: FindingData,
    MemoryKind.RUNBOOK: RunbookData,
    MemoryKind.SUMMARY: SummaryData,
    MemoryKind.PROJECT: ProjectData,
    MemoryKind.SERVICE: ServiceData,
    MemoryKind.ROUTE: RouteData,
    MemoryKind.ENVIRONMENT: EnvironmentData,
    MemoryKind.DEPENDENCY: DependencyData,
    MemoryKind.PERSON: PersonData,
}


def names_in(kind: str) -> dict[str, str]:
    """This kind's foreign keys: field name -> the kind it names.

    Read off `field(metadata={"names": ...})` rather than listed anywhere,
    so the declaration and the check cannot disagree — the same reason
    `readers_for` is derived from the kind rather than stored beside it.
    Top level only: no nested payload declares one, and a nested foreign key
    would be a shape worth arguing about before it is supported.
    """
    shape = MEMORY_DATA[MemoryKind(kind)]
    if shape is None:
        return {}
    return {
        f.name: str(f.metadata["names"])
        for f in dataclass_fields(shape)
        if f.metadata.get("names")
    }


def named_by(kind: str) -> tuple[tuple[str, str], ...]:
    """Every `(kind, field)` that names rows of `kind` — the reverse of
    `names_in`, for asking "who would this rename break?"."""
    kind = str(MemoryKind(kind))
    return tuple(
        (other.value, field)
        for other in MemoryKind
        for field, named in names_in(other).items()
        if named == kind
    )


def natural_key(kind: str, data: dict[str, Any] | None, given: str | None) -> str | None:
    """A structured kind's natural key — the spec's `key=` column — read off
    its already-validated `data`, so the key cannot disagree with the row.

    `runbook` is the one kind whose key is not in its data (it is a short
    name the operator gives), so it is the one that takes `given`, and
    refuses without it. `summary` has a fixed key, which is what makes the
    partial unique index hold it to one per room. Prose kinds and
    `decision` have none.
    """
    kind = MemoryKind(kind)
    d = data or {}
    if kind is MemoryKind.RUNBOOK:
        if not (given or "").strip():
            raise MemoryRefused("a runbook needs a key — a short name for it")
        return given.strip()
    return {
        MemoryKind.FINDING: lambda: f"{d.get('service')}:{d.get('error_code') or ''}",
        MemoryKind.SUMMARY: lambda: "room",
        MemoryKind.PROJECT: lambda: d.get("name"),
        MemoryKind.SERVICE: lambda: d.get("name"),
        MemoryKind.ROUTE: lambda: d.get("domain"),
        MemoryKind.ENVIRONMENT: lambda: d.get("suffix"),
        MemoryKind.DEPENDENCY: lambda: f"{d.get('from_service')}->{d.get('to_service')}",
        MemoryKind.PERSON: lambda: d.get("discord_id"),
    }.get(kind, lambda: None)()


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
    as a tool result. See `friday/tools/memory.py`.

    `id` is opaque and sparse rather than sequential, so a model that invents
    one fails instead of landing on a neighbouring row.

    `kind` decides who reads this row (`readers_for`, D14). `status` and
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
    #: A structured kind's natural key (`natural_key`); `None` for prose.
    key: str | None = None
    #: A structured kind's payload, already checked against `MEMORY_DATA`.
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


@dataclass(frozen=True, slots=True)
class Artifact:
    """Verbatim material a message carried — code, a stack trace, SQL, a log,
    a `curl` — stored whole and pointed at rather than paraphrased (board
    `what-the-room-already-knows`, ticket 07, D8).

    `content` is exactly what `friday.text.transform.transform` lifted out
    of the message that produced it, untouched since. A build that needs it
    back gets `content` byte for byte; a build that must never see it — the
    summariser — gets `id` and `description` only, through
    `friday.text.transform.redact`.

    `id` is opaque and sparse, the same reasoning as `Memory.id`: a model
    that invents one fails rather than landing on somebody else's artifact.

    No `deleted_at`, unlike `Memory` — nothing writes an artifact except the
    store itself, at the moment a message that carries code is first
    recorded, so there is no agent decision to take back.
    """

    id: str
    channel_id: str
    provider: str
    source_message_id: str
    content: str
    description: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FridayState:
    """What one message's journey knows about itself, the whole way down.

    Board `every-answer-has-a-shape`, D8–D10. This is what the SDK's per-run
    `context` carries, and it is the **only** thing it carries: the slot used
    to mean "who is this run about" for one agent and "where the answer will
    appear" for another, which is two mechanisms sharing one parameter.

    **It replaces `MemoryScope`**, which named the same room under a second
    name for a narrower purpose — a memory's channel, task, agent and source
    message. One notion of "which room is this", not two, because a second
    name for one thing is how the two drift (D10). Everything that made
    `MemoryScope` correct is unchanged and is the reason this is a value
    rather than a closure variable: an agent here is built once, at startup,
    and reused for every task in every channel, so a scope captured when the
    tools were made would pin every room's memory to whichever room happened
    to be first. It arrives per call. The model still cannot name it, which
    was the point.

    `channel_id` is the read *and* write boundary for memory — a memory
    written in one room is invisible in another. Not a nicety: this system's
    rooms are different teams, and a fact learned in one is a leak in the
    next.

    **Read-only, and every change is a named method** (D9). Not a style
    choice: this value is handed to a tool, to the store, and to the recording
    sink within one run, and a field anything could assign would make "what
    can change this, and where" unanswerable — which is the question the
    threading this replaces could not answer either. Each method below returns
    a *new* state, so a value handed to one step cannot be changed underneath
    another, and each changes exactly one thing. There is deliberately no
    general `with_(**fields)`: a generic setter would make every change legal
    again and put the list back out of reach.

    **Only `channel_id` and `agent` are required**, and that is not laziness
    about the rest. Those two are the boundary and the provenance: a memory
    written without a room has nowhere safe to live, and one written without
    an author loses "who wrote this, and while doing what" — the question the
    operator's board exists to answer about a line an agent is now acting on.
    Everything else is a fact the journey supplies when it has it, which is
    what `None` already meant on `task_id`: a run that belongs to no task says
    so. `for_conversation` fills the four a task's conversation already knows,
    and the named methods below add the rest as the journey learns them.
    """

    channel_id: str
    #: Which agent is running right now. Changed by `as_agent` at each
    #: hand-off, so a memory written during an extraction is not attributed to
    #: whoever ran first.
    agent: str
    #: Which provider the message came from. The other half of a message's
    #: identity key — the store dedupes on `(provider, provider_message_id)` —
    #: and `None` in a state built by hand for something that does not need
    #: it, the same way `task_id` is `None` before triage has decided.
    provider: str | None = None
    thread_id: str | None = None
    #: The message this step is about. For triage that is the mention; for the
    #: responder, the message it is drafting a reply to. It is also what the
    #: recording sink correlates a model call by, and what a memory records as
    #: the message that produced it — a tool with no message in scope leaves
    #: it `None`, and the store keeps it `None`.
    message_id: str | None = None
    author_id: str | None = None
    author_name: str | None = None
    #: The `provider_message_id` this replies to, if it is a reply.
    reply_to: str | None = None
    #: The task this message became, once it has become one. `None` for a run
    #: that belongs to no task, which is every run before triage has decided.
    task_id: int | None = None

    @classmethod
    def for_event(cls, event: "InboundEvent", *, agent: str) -> "FridayState":
        """The state a message's journey starts with.

        Seven of the nine fields are facts the inbound message already carries,
        and copying them here once is the whole point: they used to be named
        again, as a different subset, by `Triage.decide`, `Responder.draft`,
        `prepare`, `Extractor.run` and the recording sink — so adding one more
        fact meant threading one more parameter through five signatures.

        Deliberately absent until board `every-answer-has-a-shape`'s ticket 07
        gave it a caller: triage is where a journey actually starts, and a
        constructor with no caller is the speculative generality this board is
        otherwise removing.
        """
        return cls(
            channel_id=event.channel_id,
            agent=agent,
            provider=event.provider,
            thread_id=event.thread_id,
            message_id=event.provider_message_id,
            author_id=event.author_id,
            author_name=event.author_name,
            reply_to=event.reply_to,
        )

    @classmethod
    def for_conversation(
        cls, conversation: "ConversationId", *, agent: str
    ) -> "FridayState":
        """The state for work about a conversation rather than about one
        message — a task being worked on, which is what the pool has.

        `ConversationId` already is the provider-qualified place, so taking it
        whole is the same argument this class makes one level up: the pool
        used to hand a responder `channel_id`, `task_id` and `message_id` as
        three parameters and take the channel off a conversation it had in its
        hand.
        """
        return cls(
            channel_id=conversation.channel_id,
            agent=agent,
            provider=conversation.provider,
            thread_id=conversation.thread_id,
        )

    def as_agent(self, agent: str) -> "FridayState":
        """Hand the run on to a different agent."""
        return replace(self, agent=agent)

    def for_task(self, task_id: int | None) -> "FridayState":
        """The message became this task."""
        return replace(self, task_id=task_id)

    def about_message(self, message_id: str | None) -> "FridayState":
        """This step is about a different message than the last one was."""
        return replace(self, message_id=message_id)


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
    #: Database id. Read off the schema row at the store boundary so
    #: the wire can use it as a stable ordering key (e.g. for
    #: comparing a turn to the outbound rows that followed it). Not
    #: in the original dataclass because no caller used it; the
    #: Flow screen now needs it (ticket 12) to answer "did this turn
    #: produce the operator's pending question".
    id: int | None = None
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


@dataclass(frozen=True, slots=True)
class MonitorEvent:
    """A line on the Monitor screen's live feed.

    Today the only two event sources are model calls and tool calls;
    the union is open and the screen reads `kind` off `type`. The
    `id` is monotonic within the feed so the React reconciler
    never re-mounts a row it already mounted.

    `state` is the same vocabulary the Flow screen uses — done,
    failed, retrying, ok — so the audit's "same word everywhere"
    rule is one place, not many.
    """

    id: int
    type: str  # "model_call" | "tool_call"
    occurred_at: datetime
    agent: str
    tool: str | None = None
    latency_ms: int | None = None
    state: str = "done"


@dataclass(frozen=True, slots=True)
class RunningTask:
    """A task that is still being worked in.

    The Monitor screen's right-hand column renders one card per
    running task. `last_tool` is the operator's hint about what
    the agent is doing right now — a name, not a status; the
    status is `state` on the parent Task.
    """

    id: int
    type: str
    state: str
    room: str
    #: The message that opened this task — `provider:provider_message_id`.
    #: `None` when no message could be linked (a manually-seeded task).
    #: The Monitor screen reads this to build the deep-link to the
    #: flow page.
    message_id: str | None = None
    last_activity_at: datetime | None = None
    last_tool: str | None = None
    attempts: int = 0


@dataclass(frozen=True, slots=True)
class MonitorSnapshot:
    """One snapshot of the Monitor screen. The page asks for this
    on mount, then SSE (ticket 05) takes over from there."""

    status: str  # "connected" | "disconnected"
    events: list[MonitorEvent]
    running_tasks: list[RunningTask]
    messages: int
    untriaged: int
    last_message_at: datetime | None
    spend_today: int


@dataclass(frozen=True, slots=True)
class ExtractionMark:
    """What node 0's last extraction was made from, and what it came to.

    Node 0 is excluded from the checkpoint and re-executes on every pass, which
    is correct — a reporter who sends the curl three seconds later has to be
    read. What was not correct is calling a model when nothing arrived: one
    task in the recorded data has two extractor calls whose prompts share a
    sha256, seven and a half hours apart, because the task sat pending across a
    restart and every pass paid again.

    **The fingerprint is over the extractor's per-call input** — since board
    `what-the-room-already-knows`'s ticket 15 (D26), that means one thing:
    `friday.extraction.context.FullContext`, the single value node 0 gathers
    and hands to `build_input`. Every one of its fields moves the digest,
    because `input_fingerprint` hashes what `would_ask` actually renders
    from it, not a reconstruction naming some of them — the reconstruction
    is exactly what broke the day ticket 01 gave the prompt a third input it
    could not see.

    The task's parameters were not part of this at all until ticket 08's D8:
    they now shape the schema — an already-filled field drops out of what
    the model is shown, via `context.known` — so a fill changes the prompt
    and has to change the digest the same way ticket 01's room did. A
    parameter change already changed what node 0 concluded even with a
    replayed answer, because the replayed question was re-filtered against
    the parameters as they stood; that re-filtering is unchanged, it is just
    no longer the only route by which the parameters affect the outcome.

    Per-call input, not the whole prompt: the extractor's `instructions` carry
    the skill catalogue and the job text, which change on a restart rather than
    per task, and a new skill is not new information about this report.

    `params` and the clarification are kept so a skipped call is *equivalent*
    to the call, not merely cheaper: the same fill is applied and the same
    question is asked. Without the question a skip would turn an `Ask` the
    extractor raised into "everything needed is here" on the next pass, because
    the fields it asks about are usually the optional ones no structural rule
    challenges.

    Here rather than beside the node, for the reason `FridayState` is here: it
    is part of `Database`'s signature as well, and a store may not import from
    the packages above it.
    """

    fingerprint: str
    #: What the extractor produced, as a plain mapping — the params class it
    #: belongs to is the task's type, which the reader already knows.
    params: dict[str, Any] = field(default_factory=dict)
    #: The fields the extractor asked about, and why. Empty means it asked
    #: nothing, which is a different thing from having asked about nothing.
    #:
    #: `asked_about` rather than `fields`, which is what it was: this sits two
    #: lines from `params` and is read in a module that imports `fields` from
    #: `dataclasses` and calls it on a params class on the next line.
    #:
    #: Flattened rather than holding the `Clarify` it came from, because that
    #: type lives in `friday/extraction/` and this one is read by the store —
    #: `friday/domain/` may not import upward. Reassembled by its one reader.
    asked_about: tuple[str, ...] = ()
    because: str | None = None
