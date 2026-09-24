"""Rules for `Params`, and the one engine that runs them.

A rule is a callable. `validate(params)` walks every rule registered on the
params' class and returns the list of problems — empty if everything passes.

Why a closed vocabulary of rules and not "any predicate"? Because a rule that
also knows its own message is the one that survives being quoted back to the
operator. A bare predicate only knows true/false; a rule knows "this
correlation id is the wrong shape" and that is what the workflow turns into a
question.

This is the pure value layer — no friday imports, no I/O — so it lives in
`friday.sdk`, the bottom of the stack. A plugin reaches the DSL to declare its
params' rules (`ApiIssueParams._RULES`) by importing `sdk` only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


__all__ = [
    "asked_as",
    "InSet",
    "Matches",
    "NonEmpty",
    "OneOf",
    "Problem",
    "validate",
]


@dataclass(frozen=True, slots=True)
class Problem:
    """One thing wrong with the params, addressable by field name.

    `message` is empty when the field is missing entirely (structural); it
    carries the rule's text when the field is present but wrong (semantic).
    """

    field: str
    message: str = ""

    def __str__(self) -> str:
        return f"{self.field}: {self.message}" if self.message else self.field


def validate(params: Any) -> list[Problem]:
    """Run every rule in `params._RULES` and return what failed.

    An empty list means the params are usable. Rules that pass are silent;
    rules that fail each contribute one `Problem`. A class with no `_RULES`
    validates cleanly — no implicit schema discovery, no annotation parsing.

    Per-field rules call `rule.check(value)`. Cross-field rules (`OneOf`) get
    the whole params object via `rule.check_params(params)` so they can look at
    siblings.
    """
    rules: dict[str, Any] = getattr(type(params), "_RULES", {})
    problems: list[Problem] = []
    for field_name, rule in rules.items():
        if isinstance(rule, OneOf):
            message = rule.check_params(params)
        else:
            message = rule.check(getattr(params, field_name, None))
        if message is not None:
            problems.append(Problem(field=field_name, message=message))
    return problems


def asked_as(params: Any, subject: str) -> str:
    """How to ask about one thing, in the words that live beside it.

    Here rather than in the node that renders the question, and that placement
    is the point. `validate` above produces the `Problem`s whose `field` this
    resolves, off the same `type(params)._RULES` — a resolver living in
    `friday/dag/` made a second module walk that dict, and read it off the
    *instance* where this one reads the class. One module owns the rules; it
    owns how to speak about what they report.

    A field's phrase is on the field, in `ask` metadata next to the `doc` that
    tells the extractor what the field means. A cross-field rule reports under
    a sentinel — `_traceable` is not a field and cannot hold metadata — so its
    phrase is on the rule. See `OneOf` for why that is the rule rather than an
    exception to one.

    **There is no fallback, and deleting it was the point of ticket 13.** The
    dict this replaced fell back to the field name, which reads acceptably
    often enough that `project` went the life of its type with no phrase.
    Raising is loud; `test_every_askable_field_says_how_to_ask_about_it` is
    what makes it not happen. If it ever does, `Pool` turns the exception into
    a hand-over carrying this message, so it reaches the operator rather than
    dropping a mention — the author sees it in a test, the operator sees it in
    production, and neither sees a reporter asked about "the retry after".
    """
    cls = type(params) if not isinstance(params, type) else params
    field_meta = getattr(cls, "__dataclass_fields__", {}).get(subject)
    if field_meta is not None and (field_meta.metadata or {}).get("ask"):
        return (field_meta.metadata or {}).get("ask", "")
    rule = getattr(cls, "_RULES", {}).get(subject)
    phrase = getattr(rule, "ask", "") if rule is not None else ""
    if not phrase:
        raise ValueError(
            f"{cls.__name__} reports {subject!r} and says nothing about how "
            f"to ask for it — put an `ask` beside it"
        )
    return phrase


def _is_blank(value: Any) -> bool:
    """True for values the structural check should treat as missing.

    None and whitespace-only strings both count as "no value here" — the
    operator is asked once by `_missing`, not twice by the two layers.
    """
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


@dataclass(frozen=True, slots=True)
class Matches:
    """The field must match a regular expression. None and blank strings pass.

    `ask` is optional and only needed when the field it guards carries no
    `ask` of its own — a field that is filled but never asked for directly.
    `correlation_id` is the one such field (board
    `read-it-the-way-the-operator-does`, ticket 01): it is read out of the
    response the reporter pasted, so nobody is ever asked for it, and yet
    this rule can still report it as malformed. Without a phrase here
    `asked_as` raises and the run hands over instead of asking for the one
    thing that would settle it — the response, again. Same argument as
    `OneOf`'s: the phrase lives beside the thing that reports.
    """

    pattern: str
    name: str = "matches"
    #: See above. Empty means "the field has its own `ask`".
    ask: str = ""

    def check(self, value: Any) -> str | None:
        if _is_blank(value):
            return None
        if not isinstance(value, str):
            return f"expected text, got {type(value).__name__}"
        if re.search(self.pattern, value) is None:
            return f"does not match {self.name!r}"
        return None


@dataclass(frozen=True, slots=True)
class InSet:
    """The field must be one of a closed set. None and blank strings pass."""

    values: frozenset[str]

    def check(self, value: Any) -> str | None:
        if _is_blank(value):
            return None
        if value not in self.values:
            allowed = ", ".join(sorted(self.values))
            return f"must be one of: {allowed}"
        return None


@dataclass(frozen=True, slots=True)
class NonEmpty:
    """The field must be a non-empty string. None and blank strings pass.

    This rule is mostly redundant with the structural check once blank
    strings are treated as missing; it stays as a named rule for the case
    where the operator wants a field that is meaningful but cannot be
    whitespace.
    """

    def check(self, value: Any) -> str | None:
        if _is_blank(value):
            return None
        if not isinstance(value, str):
            return f"expected text, got {type(value).__name__}"
        return None


@dataclass(frozen=True, slots=True)
class OneOf:
    """At least one of the named alternatives must be satisfied.

    An alternative is a field name, or a **tuple of field names that must all
    be present together**. `("curl", ("endpoint", "identifier"))` reads "the
    curl, or the endpoint *and* one identifier" — which is D2's findability
    rule, and the reason a group exists at all (board
    `read-it-the-way-the-operator-does`, ticket 01). A flat `OneOf` could only
    say "any one of these", and under that rule an endpoint on its own
    satisfies the gate and then matches every other caller of it.

    Cross-field rule: stores no `value` of its own, so it sits in `_RULES`
    under a sentinel field name. The check sees the whole params object.
    Construction fails if the named alternatives list is empty, or if any
    group in it is — either would always report, which is not what a
    constructor that accepts no arguments is for.

    **It carries its own `ask`, and that reverses what this class used to
    say.** This is the one place that argument is written out; everywhere else
    that mentions it points here.

    The old note argued phrasing belonged in one place, and that a rule
    wording its own question would be a second place for the two to drift
    apart from. The concern was right and the conclusion was not. The one
    place was a dict in the node that renders questions, keyed by field name
    and living nowhere near any field it named — and it drifted exactly as
    predicted: `project` was askable with no entry for as long as its type
    existed, and a fallback turned its field name into a sentence so nothing
    said so. Ticket 13 put a field's phrase on the field, beside what the
    field means. That leaves a sentinel nowhere to live, because its subject
    is not a field. It lives here, on the rule that reports it.

    The rule is not "one place" but **beside the thing it describes** — and
    under that rule there is still exactly one source per question.

    Required, with no default: a rule that can report has to know how to ask.
    Omitting it is a `TypeError` from the constructor; a blank one is refused
    below, where the message can say why a rule in particular cannot fall back
    to field metadata.
    """

    #: Each entry is one alternative: a field name, or a tuple of field names
    #: that only satisfies the rule together.
    fields: tuple[str | tuple[str, ...], ...]
    #: How to ask about the thing this rule reports. Never sent verbatim —
    #: the responder writes the question a reporter reads from it.
    ask: str

    def __post_init__(self) -> None:
        if not self.fields:
            raise ValueError("OneOf needs at least one field to look at")
        if any(not alternative for alternative in self.fields):
            raise ValueError(
                "OneOf was given an empty alternative, which nothing can "
                "satisfy — name the fields it stands for"
            )
        if not self.ask.strip():
            raise ValueError(
                "OneOf needs an `ask`: it reports under a sentinel, so there "
                "is no field metadata to read a phrase off"
            )

    def check_params(self, params: Any) -> str | None:
        """`None` passes. `""` fails with nothing to add.

        The empty string is deliberate and follows `_missing`, which reports an
        absent required field as a `Problem` with no message for the same
        reason: there is nothing to say about a value that is not there beyond
        asking for it. How to ask is `self.ask` — see the class docstring for
        why it moved here from a dict in the node that renders the question.
        """
        for alternative in self.fields:
            needed = (alternative,) if isinstance(alternative, str) else alternative
            if all(not _is_blank(getattr(params, f, None)) for f in needed):
                return None
        return ""
