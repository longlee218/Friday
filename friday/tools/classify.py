"""Saying what a message is — the only two things triage may conclude.

`create_task` takes the type as a closed enum rather than there being one
tool per type: the type still comes from the model, but naming a fourth type
is adding a `Params` class, not a fourth tool. Its description is built from
those classes' own docstrings, because a tool parameter *is* an instruction
to the model and an enum member nobody defined is an instruction to guess.

Tool calling rather than a structured output type: some OpenAI-compatible
providers reject `response_format: json_schema` outright.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from friday.agent.harness import ToolContext, tool
from friday.domain.actions import Decided
from friday.domain.models import PARAMS

__all__ = ["TOOLS", "ClassifyCapture", "create_task", "skip"]

#: The types the model may name, straight off the registry — so the enum
#: cannot drift from what the system can actually open a task for.
_TASK_TYPES = Literal[tuple(PARAMS)]

#: Each type's description as the model reads it, pulled from its own `Params`
#: class docstring rather than written a second time here.
_TASK_TYPE_DOC = "\n".join(
    f"        {name}: {cls.__doc__.strip()}" for name, cls in PARAMS.items()
)


@dataclass
class ClassifyCapture:
    """Per-run scratch space, so concurrent runs cannot overwrite each
    other's decision."""

    decided: Decided | None = None


def create_task(
    ctx: ToolContext[ClassifyCapture], task_type: _TASK_TYPES, confidence: float
) -> str:
    ctx.context.decided = Decided(type=task_type, confidence=confidence)
    return "recorded"


create_task.__doc__ = f"""Open a task: there is work here for a person.

Args:
    task_type: which kind of task this is —
{_TASK_TYPE_DOC}
    confidence: How certain you are of this classification, 0 to 1.
"""

create_task = tool(create_task)


@tool
def skip(ctx: ToolContext[ClassifyCapture], confidence: float) -> str:
    """The message needs no action: social talk, salary, or anything off topic.

    Args:
        confidence: How certain you are of this classification, 0 to 1.
    """
    ctx.context.decided = Decided(type="skip", confidence=confidence)
    return "recorded"


TOOLS = [create_task, skip]
