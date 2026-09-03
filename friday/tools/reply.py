"""How an agent says what it concluded: an answer, or a refusal to give one.

Both write into a capture rather than returning prose a caller has to parse.
A sentinel was a private protocol between one prompt and one function, and a
model that wandered off it — once, wearing a Markdown code fence — had its
prose read as the reply and proposed under the operator's name.

`answer` is **the only place in this system a `Reply` is built**, which is
what makes "what reaches a reporter" checkable by reading rather than by
hoping. A test pins that.
"""

from __future__ import annotations

from dataclasses import dataclass

from friday.agent.harness import ToolContext, tool
from friday.domain.actions import Action, HandOver, Reply

__all__ = ["COMPOSE_TOOLS", "ComposeCapture", "answer", "hand_over"]


@dataclass
class ComposeCapture:
    """Per-run scratch space, so two runs at once cannot overwrite each
    other's answer."""

    action: Action | None = None


@tool
def answer(ctx: ToolContext[ComposeCapture], text: str) -> str:
    """The reply to send. Room register, examples of the operator's voice and
    any skill you fetched already shaped what you were told to say — write
    the message itself, nothing more.

    Args:
        text: the reply, ready to go out once approved.
    """
    ctx.context.action = Reply(text)
    return "recorded"


@tool
def hand_over(ctx: ToolContext[ComposeCapture], reason: str) -> str:
    """Stop here instead of composing a reply. Quote your own finding — the
    operator reads it directly; a reporter never does.

    Args:
        reason: what you found, in your own words.
    """
    ctx.context.action = HandOver(reason)
    return "recorded"


#: The pair an agent that must reach a conclusion is given. One list so
#: whoever builds the agent and whoever reads the graph name the same two
#: things once each.
COMPOSE_TOOLS = [answer, hand_over]
