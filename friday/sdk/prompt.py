"""The prompt-assembly primitives, as a plugin reaches them — an sdk re-export.

The primitives are the pure value layer and live in `friday.domain.prompt` (the
bottom of the stack, alongside the validation DSL). sdk imports domain, so this
module re-exports them: a plugin builds its instructions with
`from friday.sdk.prompt import assemble, role, job, …` and imports `sdk` only,
while sdk itself stays contracts-and-re-exports rather than implementation. The
private helpers (`_escape`, `_quoted`, the markers, the trust-boundary text) are
re-exported too, because `friday/agent/instruction_prompt.py`'s domain-aware
sections build on them. Ticket 14.
"""

from __future__ import annotations

from friday.domain.prompt import (
    _QUOTE_CLOSE,
    _QUOTE_OPEN,
    _TRUST_BOUNDARY,
    _escape,
    _quoted,
    Section,
    assemble,
    base,
    counterpart,
    critical_reminder,
    job,
    response_style,
    role,
    soul,
    thinking_style,
    trust_boundary,
    user_input,
)

__all__ = [
    "Section",
    "assemble",
    "base",
    "counterpart",
    "critical_reminder",
    "job",
    "response_style",
    "role",
    "soul",
    "thinking_style",
    "trust_boundary",
    "user_input",
]
