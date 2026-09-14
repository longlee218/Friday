"""What a decision about a task comes to.

Every graph in `friday/dag/` ends in one of these, and the pool in
`friday/tasks/` is what acts on it. They are vocabulary, not mechanism, which
is why they live here rather than in either: a graph node importing them from
the pool, or the pool importing them from the graph engine, was an import
cycle with no reason for the direction it happened to take.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from friday.domain.models import DECISIONS, PARAMS, SKIP

__all__ = [
    "Action", "Ask", "Decided", "HandOver", "NeedsHuman", "Reply", "TriageOutcome",
]


@dataclass(frozen=True, slots=True)
class Ask:
    """Ask the reporter for something. The text is ready to send."""

    text: str


@dataclass(frozen=True, slots=True)
class Reply:
    """An answer. Unlike an `Ask`, this waits for approval.

    The asymmetry is the point: asking for a correlationId costs a question if
    it is wrong, and asserting a cause costs the operator's credibility with
    their own team.
    """

    text: str


@dataclass(frozen=True, slots=True)
class HandOver:
    """Nothing can be done automatically. A human picks it up.

    `reason` is quoted to the operator, never sent to a reporter under the
    operator's name — a node's own finding ("the cause mentions a
    migration"), or code's own ("no workflow for this yet"). Named for what
    it does (ticket 06): a node's agent can call `hand_over(reason)` itself
    to report this the same way it reports an answer, and a graph that
    reaches its end without deciding anything hands over by code, with no
    model asked. `Park` was the name before there was a tool by that name to
    confuse it with.

    `interruption` is set only for one specific shape of hand-over (ticket
    07): a node's agent called a tool the SDK stopped to ask about — applying
    a fix, so far — rather than one that decided nothing could be done. It is
    the SDK's own run state, serialized, so approving resumes the exact call
    that stopped rather than restarting the investigation to reach it again.
    `None` for every ordinary hand-over, which is most of them.
    """

    reason: str
    interruption: dict[str, Any] | None = None


Action = Ask | Reply | HandOver


# --- what triage concludes --------------------------------------------------


#: What each decision means, as the model reads it. Every task type's line is
#: its own `Params` class docstring, so a fourth type is a fourth class and not
#: a fourth thing to write down — and `skip`'s line is written here because it
#: is the one decision with no class behind it.
#:
#: This text was in `friday/tools/classify.py`, split across a tool docstring
#: and a second tool that existed only to say `skip`. It is one string now, on
#: the field it describes, the way every extraction field's `doc` already is.
def _means(name: str, params_cls: type) -> str:
    """One type's line, from its own class.

    **A type that never wrote one down is refused at import**, and the check
    is not `if not doc`. A `@dataclass` always has a docstring: absent one of
    its own, Python synthesises its constructor signature, so
    `ApiIssueParams(summary: str = '', environment: str | None = None, ...)`
    is what a forgetful author would ship — to the model, as the description
    of what the type *means*, on the highest-volume path in the system. Empty
    is the case that cannot happen here; the signature is the case that can,
    and it reads like a description to everything except a person.

    Failing at import costs a restart. Shipping it costs whoever reported the
    thing, because an enum member nobody defined is an instruction to guess.
    """
    doc = (params_cls.__doc__ or "").strip()
    if not doc or doc.startswith(f"{params_cls.__name__}("):
        raise ValueError(
            f"{params_cls.__name__} has no docstring of its own, so triage "
            f"cannot be told what {name!r} means — its description would be "
            f"the class's own constructor signature, which is an instruction "
            f"to guess"
        )
    return f"    {name}: {doc}"


_TYPE_DOC = "\n".join(
    [
        "which kind of task this is, or `skip` —",
        *(_means(name, cls) for name, cls in PARAMS.items()),
        f"    {SKIP}: the message needs no action — social talk, salary, or "
        f"anything off topic.",
    ]
)


@dataclass(frozen=True, slots=True)
class Decided:
    """Triage reached a conclusion: this message is of this type.

    That is the whole of it. No parameters, no summary — triage classifies and
    stops. Lifting values out of the message is a different job with a
    different failure mode, it belongs to whoever needs those values, and
    doing both there meant two producers for one set of fields and a merge to
    reconcile them. See `friday/extraction/`.

    **This is also the shape triage answers**, and `type` is closed to
    `DECISIONS` — every task type plus `skip` (board
    `every-answer-has-a-shape`, D6). That **reverses** the recorded split
    between a `classify` tool and a `skip` tool, whose argument was that
    everything `classify` names opens work while `skip` names the absence of
    it. True, and it is `TriageRunner._apply`'s business, where it stays. What
    the split actually bought was two validations for one question: an
    invented type could reach `_apply` and open a task the pool then discovers
    has no graph, and "there is no work here" was checked less strictly than
    "there is".

    Measured, the wire does not close the set: asked for that exact enum, the
    configured provider answered `hardware_issue`. What closes it is this
    annotation, checked in this process before anybody is allowed to act on
    it.
    """

    #: **Neither field has a default, and that is the guard rather than a
    #: style.** `skip` opens nothing — `TriageRunner._apply` logs a line and
    #: returns — so it is the one decision that must never be reachable by
    #: accident. A default of `skip` made it the decision the system reached
    #: when the model said *nothing at all*: `fits` drops unknown keys, every
    #: remaining field had a default, and an empty answer validated cleanly
    #: into a silent discard. That is CLAUDE.md's "never to a silent discard",
    #: and it is the same shape as the failure this board was opened for — an
    #: unreadable answer becoming a successful one because every field had a
    #: default.
    #:
    #: The realistic trigger was not an empty object but a stale field name:
    #: `task_type` is what the deleted `classify` tool called this, so a model
    #: carrying that habit named a real type and had it dropped. Without a
    #: default, pydantic answers "Field required", which is exactly the
    #: correction the answer tool hands back.
    type: Literal[DECISIONS] = field(metadata={"doc": _TYPE_DOC})  # type: ignore[valid-type]
    confidence: float = field(
        metadata={"doc": "how certain you are of this classification, 0 to 1."}
    )


@dataclass(frozen=True, slots=True)
class NeedsHuman:
    """Triage could not conclude. The message still becomes work — never
    silence, which is indistinguishable from the system working."""

    reason: str
    #: Set when the model *did* answer and the answer named something outside
    #: `DECISIONS`, rather than the call failing or never happening (D20).
    #:
    #: A flag rather than a sentence, for the reason `Harness.refusal` is one:
    #: `reason` carries the same information in words, and a caller that has to
    #: branch on it should not be reading them. Two failures that both leave no
    #: classification and lead different places — one says the model invented a
    #: label, the other says the provider was down — and counting them together
    #: would hide the failure this board exists to make impossible.
    out_of_set: bool = False


#: What a classification comes to. Here rather than in `friday/triage/`
#: because the tool that produces it lives in `friday/tools/`, and a tool
#: importing the module that imports it is the cycle ticket 01 took out of
#: `Ask`/`Reply`/`HandOver` for exactly this reason.
TriageOutcome = Decided | NeedsHuman
