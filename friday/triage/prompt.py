"""What triage's prompt looks like, and from what it is assembled.

The whole of it, in one place. The stable half is the job text plus the
operator's vouched-for examples — appended to instructions rather than sent per
call, because examples that moved per call would cost the cache hit on
everything after them. The per-call half is the turn's messages, and nothing
else: no identity, no date, no task section. The whole output is which tool was
called and a number, and none of those would change it.
"""

from __future__ import annotations

from collections.abc import Sequence

from friday.agent.instruction_prompt import conversation, few_shot
from friday.domain.models import InboundEvent

__all__ = ["build_input", "build_instructions"]


#: The job. Whole sentences to a model, so no comments inside — anything that
#: must not ship lives up here. Nothing below names a field or a tool: the
#: tools carry their own docstrings, and that schema is the real contract.
INSTRUCTIONS = """You decide what a chat message is. Nothing else.

Call exactly one tool. Which tool you call is the answer; the only thing you
add is how certain you are of it.

Do not copy values out of the message, do not summarise it, do not answer it.
Something else reads the message for what it contains — your job is the label
and your confidence in it.

Messages about salary, personal matters, or social talk are always skip."""


def build_instructions(examples: Sequence[tuple[str, str]] = ()) -> str:
    """The job, then the classifications the operator marked right."""
    return INSTRUCTIONS + _examples_block(examples)


def build_input(events: Sequence[InboundEvent]) -> str:
    """The turn, rendered as the one section triage receives."""
    return conversation(list(events)).render()


def _examples_block(examples: Sequence[tuple[str, str]]) -> str:
    """Past classifications the operator vouched for, as few-shot examples.

    Empty when nobody has vouched for anything, which is the state a fresh
    install is in and the state it stays in until somebody reacts. That is
    deliberate: an example nobody looked at teaches the classifier its own
    habits, and the drift has no floor because every generation of examples
    is drawn from the last one's output.
    """
    if not examples:
        return ""
    return "\n\n" + few_shot(
        list(examples), verdict="what it turned out to be"
    ).render()
