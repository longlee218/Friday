"""Stopping to ask, instead of guessing.

An agent that cannot ask has two options when something is missing: refuse,
or invent. Both have shipped here. The extractor invented a `correlationId`
format; a composing node with nothing to say sent a reporter a raw diff.

This is the third option, and it is a *tool* rather than a prompt instruction
because a prompt can only ask the model to say it is confused. A tool call is
a thing the run can stop on — the same shape `answer` and `hand_over` already
use, and the same shape `apply_fix` uses to wait for the operator.

Which of the two audiences the question reaches is not this tool's business.
It records what was asked and why; `Pool._route` already knows that an `Ask`
goes to the reporter and a `HandOver` goes to the operator, and it is the one
place that should know.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from friday.agent.harness import ToolContext, tool
from friday.domain.actions import Ask

__all__ = ["CLARIFICATION_TYPES", "Clarification", "ClarifyCapture", "ask_clarification"]

#: Why the agent is stopping. A closed set, because the tool schema *is* the
#: instruction: an open string invites the model to write a sentence here, and
#: a sentence is not something the operator can count later.
ClarificationType = Literal[
    #: Something required was not given at all.
    "missing_info",
    #: More than one reading of the request is valid.
    "ambiguous_requirement",
    #: Several valid ways to do it, and the choice is not the agent's.
    "approach_choice",
    #: The action changes something that is hard to undo.
    "risk_confirmation",
    #: The agent has a recommendation and wants a yes before acting on it.
    "suggestion",
]

CLARIFICATION_TYPES = (
    "missing_info",
    "ambiguous_requirement",
    "approach_choice",
    "risk_confirmation",
    "suggestion",
)


@dataclass(frozen=True, slots=True)
class Clarification:
    """What was asked, and why. Kept whole rather than flattened into the
    question text: the *type* is the part an operator can count across a week
    to see what the agent keeps not being told."""

    question: str
    kind: str
    context: str = ""
    options: tuple[str, ...] = ()

    def as_ask(self) -> Ask:
        """The question as the reporter will read it. Options are offered
        rather than described — somebody answering "which environment?" should
        be able to read the list and pick, not compose a sentence."""
        parts = [self.question]
        if self.options:
            parts.append(" / ".join(self.options))
        return Ask("\n".join(parts))


@dataclass
class ClarifyCapture:
    """Per-run scratch space, the same pattern as the graph's own tools: the
    tool writes here rather than to a module global, so two runs at once
    cannot overwrite each other."""

    clarification: Clarification | None = None


@tool
def ask_clarification(
    ctx: ToolContext[ClarifyCapture],
    question: str,
    clarification_type: ClarificationType,
    context: str = "",
    options: list[str] | None = None,
) -> str:
    """Stop and ask, before doing any of the work.

    Call this the moment you notice something is unclear, missing or
    ambiguous — not after starting. Whoever answers gets your question
    verbatim, so ask the one thing you actually need.

    Args:
        question: what you need to know, in one sentence.
        clarification_type: why you are stopping.
        context: why you need it, if that is not obvious from the question.
        options: the choices, when you are asking somebody to pick one.
    """
    ctx.context.clarification = Clarification(
        question=question,
        kind=clarification_type,
        context=context,
        options=tuple(options or ()),
    )
    return "asked"
