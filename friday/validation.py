"""Rules for `Params`, and the one engine that runs them.

A rule is a callable. `validate(params)` walks every rule registered on the
params' class and returns the list of problems — empty if everything passes.

Why a closed vocabulary of rules and not "any predicate"? Because a rule that
also knows its own name and message is the one that survives being quoted back
to the operator. A bare predicate only knows true/false; a rule knows "this
correlation id is the wrong shape" and that is what the workflow turns into a
question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol


__all__ = [
    "InSet",
    "Matches",
    "NonEmpty",
    "OneOf",
    "Problem",
    "Rule",
    "validate",
]


class Rule(Protocol):
    """Anything that says yes/no to a value, with a name and a failure message."""

    @property
    def name(self) -> str: ...

    def check(self, value: Any) -> str | None:
        """None if the value passes, a human-readable problem otherwise."""
        ...


@dataclass(frozen=True, slots=True)
class Problem:
    """One thing wrong with the params, addressable by field name."""

    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


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


@dataclass(frozen=True, slots=True)
class Matches:
    """The field must match a regular expression. None passes."""

    pattern: str
    name: str = "matches"

    def check(self, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            return f"expected text, got {type(value).__name__}"
        if re.search(self.pattern, value) is None:
            return f"does not match {self.name!r}"
        return None


@dataclass(frozen=True, slots=True)
class InSet:
    """The field must be one of a closed set. None passes."""

    values: frozenset[str]
    name: str = "in set"

    def check(self, value: Any) -> str | None:
        if value is None:
            return None
        if value not in self.values:
            allowed = ", ".join(sorted(self.values))
            return f"must be one of: {allowed}"
        return None


@dataclass(frozen=True, slots=True)
class NonEmpty:
    """The field must be a non-empty string. None passes."""

    name: str = "non-empty"

    def check(self, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            return "is empty"
        return None


@dataclass(frozen=True, slots=True)
class OneOf:
    """At least one of the named fields must have a value.

    Cross-field rule: stores no `value` of its own, so it sits in `_RULES`
    under a sentinel field name. The check sees the whole params object.
    """

    fields: tuple[str, ...]
    name: str = "one of"

    def check(self, value: Any) -> str | None:
        # Per-field path is not meaningful; `validate` calls `check_params`.
        return None

    def check_params(self, params: Any) -> str | None:
        if any(getattr(params, f, None) for f in self.fields):
            return None
        listed = " or ".join(self.fields)
        return f"need at least one of: {listed}"
