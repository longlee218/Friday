"""What triage concludes about a message — the outcome value types.

`Decided`/`NeedsHuman` are what a classification comes to, and `make_decided`
builds the closed schema the harness validates against, from the registry's
`task_type -> Params` mapping. Kernel-internal vocabulary, not a plugin's
contract: the workflow *actions* a plugin's graph returns (`Ask`/`Reply`/
`HandOver`) are the sdk's (`friday.sdk.actions`); these are the kernel's, because
triage is the kernel deciding which registered plugin a message belongs to.

They lived beside the actions in `friday/domain/actions.py` before domain was
folded under the kernel (DESIGN-v2 §13).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, make_dataclass
from functools import lru_cache
from typing import Any, Literal

from friday.kernel.domain.tasks import SKIP

__all__ = ["Decided", "NeedsHuman", "TriageOutcome", "make_decided"]


#: What each decision means, as the model reads it. Every task type's line is
#: its own `Params` class docstring, so a fourth type is a fourth class and not
#: a fourth thing to write down — and `skip`'s line is written here because it
#: is the one decision with no class behind it.
#:
#: This text was in `friday/kernel/tools/classify.py`, split across a tool docstring
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
    # One line per label, whatever shape the class's own docstring has: a
    # definition long enough to be worth writing is worth wrapping in the
    # source, and a raw newline here would break the enum's layout — the
    # second and later lines would sit at column zero, reading as prose
    # about nothing rather than as this label's meaning.
    return f"    {name}: {' '.join(doc.split())}"


def _type_doc(params_by_type: Mapping[str, type]) -> str:
    """The `type` field's description the classifier reads: one line per task
    type from its own `Params` docstring, plus `skip`. Built from the registry's
    task types, so a plugin's type describes itself and the core names none."""
    return "\n".join(
        [
            "which kind of task this is, or `skip` —",
            *(_means(name, cls) for name, cls in params_by_type.items()),
            f"    {SKIP}: nobody is asking you for anything — social talk, "
            f"thanks, salary, personal matters, or people talking among "
            f"themselves.",
        ]
    )


@dataclass(frozen=True, slots=True)
class Decided:
    """Triage reached a conclusion: this message is of this type.

    That is the whole of it. No parameters, no summary — triage classifies and
    stops. Lifting values out of the message is a different job with a
    different failure mode, it belongs to whoever needs those values, and
    doing both there meant two producers for one set of fields and a merge to
    reconcile them. See `friday/kernel/extraction/`.

    **The value type carries a bare `type: str`** (ticket 11). The *closed set*
    the model is held to is not baked in here — it is every registered task type
    plus `skip`, which is known only once the registry is filled at boot. So the
    schema the model answers against is built then, by `make_decided`, and its
    validated result is converted into this value. A model naming something
    outside the set is refused by that boot schema before anyone acts on it,
    exactly as the old static `Literal` did — the check just moved to where the
    set is known.
    """

    #: **Neither field has a default, and that is the guard rather than a
    #: style.** `skip` opens nothing — `TriageRunner._apply` logs a line and
    #: returns — so it is the one decision that must never be reachable by
    #: accident. A default of `skip` made it the decision the system reached
    #: when the model said *nothing at all*, which is CLAUDE.md's "never to a
    #: silent discard". Without a default, the boot schema answers "Field
    #: required", which is exactly the correction the answer tool hands back.
    type: str
    confidence: float


def _decided_field(name: str, annotation: Any, doc: str) -> tuple[str, Any, Any]:
    return (name, annotation, field(metadata={"doc": doc}))


@lru_cache(maxsize=None)
def _make_decided(items: tuple[tuple[str, type], ...]) -> type:
    params_by_type = dict(items)
    names = (*params_by_type, SKIP)
    schema = make_dataclass(
        "Decided",
        [
            _decided_field("type", Literal[names], _type_doc(params_by_type)),  # type: ignore[valid-type]
            _decided_field(
                "confidence", float, "how certain you are of this classification, 0 to 1."
            ),
        ],
        frozen=True,
        slots=True,
    )
    schema.__module__ = __name__
    schema.__doc__ = (
        "The shape triage answers, built at boot from the registry: `type` is "
        "closed to the registered task types plus `skip`. Validated by the "
        "harness, then converted to a `Decided` value."
    )
    return schema


def make_decided(params_by_type: Mapping[str, type]) -> type:
    """The structured shape the triage harness validates a classification
    against, built from the registry's `task_type -> Params` mapping.

    `type` is a `Literal` closed to those types plus `skip`, and each carries its
    own description (`_type_doc`) — the same closed set and per-label docs the
    static `Literal[DECISIONS]` gave, now sourced from the registry rather than a
    hand-maintained `PARAMS`. **Memoised by the exact mapping**, so equal sets
    yield the identical class and `isinstance`/`==` hold across callers (the boot
    schema and a test that rebuilds it are the same object).
    """
    return _make_decided(tuple(params_by_type.items()))


@dataclass(frozen=True, slots=True)
class NeedsHuman:
    """Triage could not conclude. The message still becomes work — never
    silence, which is indistinguishable from the system working."""

    reason: str
    #: Set when the model *did* answer and the answer named something outside
    #: `DECISIONS`, rather than the call failing or never happening (D20).
    #:
    #: A flag rather than a sentence, for the reason `Harness.unfit` is one:
    #: `reason` carries the same information in words, and a caller that has to
    #: branch on it should not be reading them. Two failures that both leave no
    #: classification and lead different places — one says the model invented a
    #: label, the other says the provider was down — and counting them together
    #: would hide the failure this board exists to make impossible.
    out_of_set: bool = False


#: What a classification comes to. Here rather than in `friday/kernel/triage/`
#: because the tool that produces it lives in `friday/kernel/toolsets/`, and a tool
#: importing the module that imports it is the cycle ticket 01 took out of
#: `Ask`/`Reply`/`HandOver` for exactly this reason.
TriageOutcome = Decided | NeedsHuman
