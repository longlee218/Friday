"""Saying what a message is — the only two things triage may conclude.

`classify` takes the type as a closed enum rather than there being one tool
per type: the type still comes from the model, but naming a fourth type is
adding a `Params` class, not a fourth tool. Its description is built from
those classes' own docstrings, because a tool parameter *is* an instruction
to the model and an enum member nobody defined is an instruction to guess.

**It was called `create_task`, and it creates nothing.** It records a
`Decided` in a capture and returns `"recorded"` — its own return value said
so. The task is opened by `TriageRunner._apply`, several hundred lines away,
and only sometimes: `skip` opens none, a follow-up opens none, low confidence
opens one in a different state. A tool name is an instruction to the model, so
a model told to "create a task" believed it was doing something it was not.
The module's own comment defended the distinction — "a tool named
`create_task` that creates nothing would be lying in its name" — while being
exactly that case.

Tool calling rather than a structured output type: some OpenAI-compatible
providers reject `response_format: json_schema` outright.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from friday.agent.harness import ToolContext, tool
from friday.domain.actions import Decided
from friday.domain.models import PARAMS

__all__ = ["TOOLS", "ClassifyCapture", "classify", "skip"]

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


def classify(
    ctx: ToolContext[ClassifyCapture], task_type: _TASK_TYPES, confidence: float
) -> str:
    ctx.context.decided = Decided(type=task_type, confidence=confidence)
    return "recorded"


classify.__doc__ = f"""Say what this message is. There is work here for a person.

Args:
    task_type: which kind of task this is —
{_TASK_TYPE_DOC}
    confidence: How certain you are of this classification, 0 to 1.
"""

classify = tool(classify)


@tool
def skip(ctx: ToolContext[ClassifyCapture], confidence: float) -> str:
    """The message needs no action: social talk, salary, or anything off topic.

    Its own tool rather than a `classify(type="skip")`, because the two differ
    in what follows: everything `classify` names opens work for somebody, and
    this names the absence of it.

    Args:
        confidence: How certain you are of this classification, 0 to 1.
    """
    ctx.context.decided = Decided(type="skip", confidence=confidence)
    return "recorded"


TOOLS = [classify, skip]
