"""`docs.doc_question`'s parameters — the plugin's own, against the sdk.

Moved out of the core models (now `friday.kernel.domain.models`) in ticket 15, the same way `api_issue`'s
params moved in ticket 14: a persona owns its own parameter shape, and the core
`Params` union no longer names it. A plain frozen dataclass whose field metadata
(`doc`, `ask`) the extractor and the ask-renderer read — no import of anything
but the stdlib, so the plugin stays `friday.sdk`-only (it needs no sdk symbol
here at all).
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["DocQuestionParams"]


@dataclass(frozen=True, slots=True)
class DocQuestionParams:
    """Someone asks where something is written down, or what a document,
    spec or runbook says: the answer is a pointer to writing, or a line out
    of it. They have not run anything and are reporting no behaviour. If
    they tried something and it did not do what they expected, or they ask
    what an endpoint is for and how its rules work, that is `devops.api_issue`."""

    question: str = field(
        default="",
        metadata={
            "doc": "What they want to know, kept close to their own phrasing "
            "— rewording a question changes it.",
            "ask": "what you would like to know",
        },
    )
    doc_ref: str | None = field(
        default=None,
        metadata={
            "doc": "The document, spec or page they referred to, if they "
            "named one. null if none.",
            "ask": "which document you mean",
        },
    )
