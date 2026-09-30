"""Scrubbing credentials out of anything written down — the pure value-layer core.

`scrub` is a pure, dependency-free primitive at the bottom of the stack
(`friday.sdk`), so a plugin reaches it importing `sdk` only — the same shape as
`friday.sdk.validation`. A graph node that writes a diagnosis or a report may
carry a provider error quoting an Authorization line, and scrubs it before it is
stored. The logging filter, the excepthook and the traceback renderer that *use*
`scrub` stay in `friday/kernel/ops/redact.py`, which re-exports it.

Two ways a secret is caught, and both matter (DESIGN-v2 §12, §3.2):

- **By pattern** — the shapes below. Deliberately broad: a false positive costs
  a few unreadable characters in a log line; a false negative is an account.
- **By value** — the exact secrets this deployment was configured with (agent
  API keys, an MCP server's declared env, the Discord tokens). A key that does
  not match any shape above still must not be written down, so the composition
  root registers the literal values it holds and `scrub` redacts them too. This
  is the enforced control §3.2 names "value-based redaction of declared
  secrets"; the pattern list is the convention that catches the rest.

The value registry is process-global and set once at boot. It is pure data — no
I/O, no config knowledge — so this stays a contracts-only module: the composition
root reads the secrets and calls `register_secret_values`; `scrub` here only
substitutes what it was handed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

__all__ = [
    "clear_secret_values",
    "matches_secret_pattern",
    "matches_secret_value",
    "register_secret_values",
    "scrub",
]

_SECRETS = re.compile(
    r"""(
        \bsk-[A-Za-z0-9_-]{16,}                  # OpenAI-style keys
      | \bBearer\s+[A-Za-z0-9._~+/=-]{16,}       # Authorization headers
      | \beyJ[A-Za-z0-9._-]{20,}                 # JWTs, and Discord tokens
      | \b[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}  # Discord bot
    )""",
    re.VERBOSE,
)

_REDACTED = "[REDACTED]"

#: The exact secret values this deployment holds, longest first so a value that
#: contains a shorter one is redacted whole rather than leaving a tail. Set by
#: `register_secret_values` at boot; empty until then, which is the right answer
#: for every test that has not declared a secret.
_secret_values: tuple[str, ...] = ()


def register_secret_values(values: Iterable[str]) -> None:
    """Declare the literal secrets to redact by value. Idempotent — the boot
    calls it once with everything it holds. Blank and one-character strings are
    dropped: they are not secrets and would blank out ordinary text."""
    global _secret_values
    kept = {v for v in values if isinstance(v, str) and len(v.strip()) > 1}
    _secret_values = tuple(sorted(kept, key=len, reverse=True))


def clear_secret_values() -> None:
    """Forget every registered value. For tests, which must not leak a declared
    secret into the process the next test runs in."""
    global _secret_values
    _secret_values = ()


def scrub(text: str) -> str:
    """Redact both the declared secret values and the pattern matches.

    Values first: a configured key that also happens to match a pattern is still
    redacted, and one that matches nothing would otherwise slip through.
    """
    for value in _secret_values:
        text = text.replace(value, _REDACTED)
    return _SECRETS.sub(_REDACTED, text)


def matches_secret_pattern(text: str) -> bool:
    """Whether the text contains something shaped like a credential — used to
    flag a draft on the approval card, before `scrub` rewrites it."""
    return _SECRETS.search(text) is not None


def matches_secret_value(text: str) -> bool:
    """Whether the text contains a declared secret verbatim."""
    return any(value in text for value in _secret_values)
