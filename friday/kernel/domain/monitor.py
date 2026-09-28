"""What the Monitor screen reads: calls, message flows, events and the snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from friday.kernel.domain.messages import InboundEvent
from friday.kernel.domain.outbound import Outbound
from friday.kernel.domain.tasks import RunningTask, Task


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One thing an agent reached for, and what came back.

    Beside `ModelCall` rather than inside it: a prompt says what an agent was
    *asked*, and says nothing about what it did. Which of the four skill tools
    an agent actually reaches for — the catalogue by name, or the search when
    the catalogue's wording did not surface it — was a question nothing could
    answer, and it becomes an expensive one the day a tool leaves this process
    with arguments a model chose.

    `failed` rather than an error string, because a tool that fails does not
    reach the recorder as a failure: the run's tool-failure hook turns it into a
    "carry on without it" message for the model, an ordinary *result*. Without
    this flag a failure is indistinguishable from an answer that reads like one.
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
