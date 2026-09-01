"""Cleaning and recovering parameter values from a message.

Two things the live provider taught us. The model emits the *string* "null"
often enough that an unnormalised value will be mistaken for a real one — and a
workflow that thinks it has a correlationId will never ask for the one it needs.

And the parameters that matter most are the ones a regex finds exactly. Letting
the message decide, with the model as fallback, makes extraction immune to model
quality: the difference between MiniMax M2.7 and M3 stops mattering for the
fields that carry the work.
"""

from __future__ import annotations

import re

__all__ = ["clean", "find_correlation_id", "find_curl", "find_environment"]

_ABSENT = {"", "null", "none", "nil", "n/a", "na", "undefined", "-"}

# A labelled identifier only. An unlabelled token is far too easy to mistake for
# an id, and a wrong correlationId is worse than a missing one: it sends the
# tracing step looking for something that never existed.
_CORRELATION = re.compile(
    r"""(?:x-)?(?:correlation|trace|request|req)[\s_-]*id     # the label
        \s*[:=]?\s*                                          # optional punctuation
        (?:\b(?:is|was|la|l\u00e0)\b\s*)?                        # or a connecting word
        ["']?([A-Za-z0-9][A-Za-z0-9_-]{3,})["']?              # the value
    """,
    re.IGNORECASE | re.VERBOSE,
)

_ENVIRONMENTS = (
    "production", "prod", "staging", "stg", "uat",
    "sandbox", "development", "dev",
)
# Whole words only: "dev" must not match inside "developer".
_ENVIRONMENT = re.compile(
    r"\b(" + "|".join(_ENVIRONMENTS) + r")\b", re.IGNORECASE
)

_CURL = re.compile(r"curl\s+[^\n`]+", re.IGNORECASE)


def clean(value: str | None) -> str | None:
    """Trim a value, treating absent-looking text as absent."""
    if value is None:
        return None
    trimmed = value.strip()
    return None if trimmed.lower() in _ABSENT else trimmed


def find_correlation_id(text: str) -> str | None:
    match = _CORRELATION.search(text)
    return match.group(1) if match else None


def find_environment(text: str) -> str | None:
    """The first environment named, in the message's own words.

    Not normalised to a canonical form: "prod" and "production" are reported as
    written, because the tracing step needs what the reporter actually said.
    """
    match = _ENVIRONMENT.search(text)
    return match.group(1).lower() if match else None


def find_curl(text: str) -> str | None:
    match = _CURL.search(text)
    return match.group(0).strip() if match else None
