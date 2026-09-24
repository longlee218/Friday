"""`scrub`, as a plugin reaches it — an sdk re-export.

The redaction primitive is the pure value layer and lives in
`friday.domain.redact` (the bottom of the stack, alongside the validation DSL).
sdk imports domain, so this module re-exports it: a plugin scrubs a value with
`from friday.sdk.redact import scrub` and imports `sdk` only, keeping sdk itself
contracts-and-re-exports rather than implementation. Ticket 14.
"""

from __future__ import annotations

from friday.domain.redact import scrub

__all__ = ["scrub"]
