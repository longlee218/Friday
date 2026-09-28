"""`backend.answer_question` — its task type name and its parameters.

Was the `docs` plugin's (ticket 15) until build-the-spine ticket 02 folded that
plugin into `backend`. Its whole graph is the shared node-0 (extract, then ask
for what is missing or hand over), built through `api.caps.simple_dag`. The
params are a plain frozen dataclass whose field metadata (`doc`, `ask`) the
extractor and the ask-renderer read — stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["TASK_TYPE", "DocQuestionParams"]

TASK_TYPE = "backend.answer_question"


@dataclass(frozen=True, slots=True)
class DocQuestionParams:
    """Someone asks where something is written down, or what a document,
    spec or runbook says: the answer is a pointer to writing, or a line out
    of it. They have not run anything and are reporting no behaviour. If
    they tried something and it did not do what they expected, or they ask
    what an endpoint is for and how its rules work, that is `backend.trace_problem`."""

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
