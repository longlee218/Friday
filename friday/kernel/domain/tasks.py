"""A piece of work derived from a mention, and what it carries.

`Params`, `SKIP`, `askable_fields` and `ExtractionMark` go with the extractor
(build-the-spine ticket 16), which brings this file back under 200 lines."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from friday.kernel.domain.conversation import ConversationId


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


# ---- task parameters -------------------------------------------------

"""What each kind of task carries.

Here rather than in `friday.kernel.triage`, which is where they were: a workflow
reached for the schema of a task's parameters *through the agent that happens
to fill them in*. The types describe the work, not the thing that recognised
it — and their annotations are read directly to decide what a task cannot
proceed without.
"""


#: Note there is no `SkipParams`. A skip opens no task, so it has no
#: parameters to carry — triage says `skip` and the message is recorded as
#: having been looked at. There was one, holding a `reason`, until triage
#: stopped producing anything but a type and a confidence.
#:
#: No member now: every task type's params are a plugin's own (`plugins/backend`,
#: `plugins/ops`; build-the-spine ticket 02), and the kernel may not import one.
#: Each is a frozen dataclass whose fields every consumer (extraction,
#: validation, the prompt renderer) reads directly, so the alias names the role,
#: not a type.
Params = Any

#: The one decision that opens no work. Everything a task type names is work;
#: this names the absence of one, and it is not a task type — so it lives here,
#: in the value layer, rather than being registered like one.
SKIP = "skip"

#: The task-type catalog (`task_type -> Params`) and the closed set triage may
#: conclude are no longer written down here (ticket 11). Each task type
#: registers a `TaskTypeSpec` into `friday.kernel.dag.registry`, and the set is read
#: from there — `friday.kernel.dag.registry.decision_params()` for the catalog,
#: `friday.kernel.domain.triage.make_decided` for the classifier's closed set (built
#: from the catalog plus `SKIP` at boot). This module stays below the registry,
#: so the values reach it as arguments, never an import.

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
    **an `ask` is what makes a field askable**. That used to be `MODEL_AUTHORED`
    — the one field nobody would be asked for was `summary`, which the model
    writes — and it stopped being the whole story in ticket 01, which added a
    field the reporter has and is still never asked for: `correlation_id` is
    read out of the response they pasted, and asking them for it directly is
    the mistake the reshape exists to undo. The two filters agree on every
    field that has an `ask`, so this is the same set it always was plus that
    one exclusion; the phrase beside the field is now the single thing that
    decides, rather than a second list to keep in step with it.
    """
    from dataclasses import fields as _dataclass_fields

    return tuple(
        f.name
        for f in _dataclass_fields(params_cls)
        if f.name not in MODEL_AUTHORED and (f.metadata or {}).get("ask")
    )


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
    `friday.kernel.extraction.context.FullContext`, the single value node 0 gathers
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
    #: type lives in `friday/kernel/extraction/` and this one is read by the store —
    #: `friday/kernel/domain/` may not import upward. Reassembled by its one reader.
    asked_about: tuple[str, ...] = ()
    because: str | None = None
