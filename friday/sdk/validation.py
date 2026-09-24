"""The params validation DSL, as a plugin reaches it — an sdk re-export.

The rules and their engine are the pure value layer and live in
`friday.domain.validation` (the bottom of the stack). sdk imports domain, so
this module re-exports them: a plugin declares its params' rules with
`from friday.sdk.validation import InSet, Matches, OneOf` and imports `sdk`
only, while the in-core params keep importing `friday.domain.validation`
directly. Ticket 14.
"""

from __future__ import annotations

from friday.domain.validation import (
    InSet,
    Matches,
    NonEmpty,
    OneOf,
    Problem,
    asked_as,
    validate,
)

__all__ = [
    "asked_as",
    "InSet",
    "Matches",
    "NonEmpty",
    "OneOf",
    "Problem",
    "validate",
]
