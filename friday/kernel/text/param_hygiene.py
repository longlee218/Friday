"""Cleaning a parameter value the model produced.

One thing the live provider taught us: it emits the *string* "null" often
enough that an unnormalised value will be mistaken for a real one, and a
workflow that thinks it has a correlationId will never ask for the one it
needs.

There were three regex finders here — `find_correlation_id`,
`find_environment`, `find_curl` — meant to let the message decide with the
model as fallback, "immune to model quality". They were never wired to
anything, and the premise is gone: they were written when *triage* extracted,
with a model that could not (MiniMax M2.7). Extraction is its own step now with
its own prompt, schema and model, and if it finds a correlationId badly the fix
is that prompt or that model — one place.

Wiring them would have put two producers on one field again. That is the shape
that needed `_merge` to reconcile it, and `_merge` is why the operator got
nineteen direct messages about one report.
"""

from __future__ import annotations

__all__ = ["clean"]

_ABSENT = {"", "null", "none", "nil", "n/a", "na", "undefined", "-"}


def clean(value: str | None) -> str | None:
    """Trim a value, treating absent-looking text as absent."""
    if value is None:
        return None
    trimmed = value.strip()
    return None if trimmed.lower() in _ABSENT else trimmed
