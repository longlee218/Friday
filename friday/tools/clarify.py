"""Stopping to ask, instead of guessing.

An agent that cannot ask has two options when something is missing: refuse,
or invent. Both have shipped here. The extractor invented a `correlationId`
format; a composing node with nothing to say sent a reporter a raw diff.

This is the third option, and it is a *tool* rather than a prompt instruction
because a prompt can only ask the model to say it is confused. A tool call is
a thing the run can stop on, which a sentence in a prompt is not.

**No agent is given it yet.** It is here for when one is.

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

#: Why the agent is stopping — the name, and what it means. **One source.**
#: The meanings used to live in comments above a bare `Literal`, so the model
#: was shown five strings and the line "why you are stopping" and had to guess
#: what `approach_choice` meant as against `ambiguous_requirement`. Triage had
#: already solved this: its task types carry their descriptions into the tool
#: schema, because a tool parameter *is* an instruction to the model, and an
#: enum member nobody defined is an instruction to guess.
CLARIFICATION_TYPES: dict[str, str] = {
    "missing_info": (
        "something required was not given at all — you cannot proceed "
        "without it, and no reading of the request supplies it"
    ),
    "ambiguous_requirement": (
        "more than one reading of the request is valid, and they lead to "
        "different work"
    ),
    "approach_choice": (
        "you know what to do but there are several valid ways, and the "
        "choice belongs to whoever asked, not to you"
    ),
    "risk_confirmation": (
        "you are about to change something that is hard to undo, and nobody "
        "has said yes to that specific change"
    ),
    "suggestion": (
        "you have a recommendation and want a yes before acting on it — "
        "nothing is blocking you, you are offering"
    ),
}

#: Built from the same dict, so a sixth kind is a sixth entry and cannot be
#: added to one of them without the other.
ClarificationType = Literal[tuple(CLARIFICATION_TYPES)]  # type: ignore[valid-type]

_TYPE_DOC = "\n".join(
    f"        {name}: {meaning}" for name, meaning in CLARIFICATION_TYPES.items()
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


def ask_clarification(
    ctx: ToolContext[ClarifyCapture],
    question: str,
    clarification_type: ClarificationType,
    context: str | None = None,
    options: list[str] | None = None,
) -> str:
    ctx.context.clarification = Clarification(
        question=question,
        kind=clarification_type,
        context=context or "",
        options=tuple(options or ()),
    )
    return "asked"


#: Written out rather than left as a docstring literal, because the kinds and
#: their meanings come from `CLARIFICATION_TYPES` — the same trick
#: `friday/tools/classify.py` uses on `classify`, for the same reason.
#:
#: **Both trailing parameters say `null` out loud.** A Python default does not
#: make a parameter optional to the model: `strict_mode` is on, and a strict
#: schema lists every property as required — `context: str = ""` was in
#: `required` with no way to express having nothing to add, so an agent told
#: "if that is not obvious" had to invent something to satisfy a schema it
#: could not opt out of. `str | None` is what puts the null branch in.
ask_clarification.__doc__ = f"""Stop and ask, before doing any of the work.

Call this the moment you notice something is unclear, missing or ambiguous —
not after starting. Whoever answers gets your question verbatim, so ask the
one thing you actually need, and ask nothing else in the same turn.

Args:
    question: what you need to know, in one sentence.
    clarification_type: why you are stopping —
{_TYPE_DOC}
    context: why you need it, if that is not obvious from the question —
        null when the question speaks for itself. Do not write a sentence
        here to fill the field.
    options: the choices, when you are asking somebody to pick one, and null
        when you are not.
"""

ask_clarification = tool(ask_clarification)
