"""Scrubbing credentials out of anything written down — the pure value-layer core.

`scrub` is a pure, dependency-free primitive at the bottom of the stack
(`friday.sdk`), so a plugin reaches it importing `sdk` only — the same shape as
`friday.sdk.validation`. A graph node that writes a diagnosis or a report may
carry a provider error quoting an Authorization line, and scrubs it before it is
stored. The logging filter, the excepthook and the traceback renderer that *use*
`scrub` stay in `friday/kernel/ops/redact.py`, which re-exports it.

Deliberately broad. A false positive costs a few unreadable characters in a
log line; a false negative is an account.
"""

from __future__ import annotations

import re

__all__ = ["scrub"]

_SECRETS = re.compile(
    r"""(
        \bsk-[A-Za-z0-9_-]{16,}                  # OpenAI-style keys
      | \bBearer\s+[A-Za-z0-9._~+/=-]{16,}       # Authorization headers
      | \beyJ[A-Za-z0-9._-]{20,}                 # JWTs, and Discord tokens
      | \b[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}  # Discord bot
    )""",
    re.VERBOSE,
)


def scrub(text: str) -> str:
    return _SECRETS.sub("[REDACTED]", text)
