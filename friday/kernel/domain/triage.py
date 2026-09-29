"""What triage concludes about a message — the outcome value types.

`Decided`/`NeedsHuman` are what a classification comes to, and `make_decided`
builds the closed schema the harness validates against, from the registered
action names. Kernel-internal vocabulary, not a plugin's
contract: the workflow *actions* a plugin's graph returns (`Ask`/`Reply`/
`HandOver`) are the sdk's (`friday.sdk.actions`); these are the kernel's, because
triage is the kernel deciding which registered plugin a message belongs to.

They lived beside the actions in `friday/domain/actions.py` before domain was
folded under the kernel (DESIGN-v2 §13).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, make_dataclass
from functools import lru_cache
from typing import Any, Literal

from friday.kernel.domain.tasks import SKIP

__all__ = ["Decided", "NeedsHuman", "TriageOutcome", "make_decided"]


#: The `type` field's description. Label meaning lives in the prompt only
#: (board `domains-plug-in`, ticket 02 §2): every action's `Recognition` is
#: rendered under `<labels>`, and the schema just closes the set.
TYPE_DOC = "one of the labels above: which kind of work this is, or `skip`."


@dataclass(frozen=True, slots=True)
class Decided:
    """Triage reached a conclusion: this message is of this type.

    That is the whole of it. No parameters, no summary — triage classifies and
    stops. Lifting values out of the message is a different job with a
    different failure mode, it belongs to whoever needs those values, and
    doing both there meant two producers for one set of fields and a merge to
    reconcile them. See `friday/kernel/extraction/`.

    **The value type carries a bare `type: str`** (ticket 11). The *closed set*
    the model is held to is not baked in here — it is every registered action
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
def _make_decided(names: tuple[str, ...]) -> type:
    schema = make_dataclass(
        "Decided",
        [
            _decided_field("type", Literal[(*names, SKIP)], TYPE_DOC),  # type: ignore[valid-type]
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
        "closed to the registered actions plus `skip`. Validated by the "
        "harness, then converted to a `Decided` value."
    )
    return schema


def make_decided(names: Iterable[str]) -> type:
    """The structured shape the triage harness validates a classification
    against, built from the registered action names.

    `type` is a `Literal` closed to those names (sorted, for stable schema
    bytes) plus `skip`, so a model naming anything else is refused before
    anyone acts on it (`out_of_set`). **Memoised by the sorted set**, so equal
    sets yield the identical class and `isinstance`/`==` hold across callers.
    """
    return _make_decided(tuple(sorted(set(names))))


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
