"""Proposing a code change — the one tool in this system that waits.

`needs_approval=True` is the whole point: the run stops holding its own
state, the operator sees exactly this diff, and nothing is applied until they
say so. Every other gate here is a row at the outbox; this one is the SDK's,
because a patch is not a message and does not wait in the same queue.
"""

from __future__ import annotations

from friday.agent.harness import tool
from friday.tools.reply import hand_over

__all__ = ["FIX_TOOLS", "apply_fix"]


@tool(needs_approval=True)
def apply_fix(diff: str) -> str:
    """Propose this diff as the fix. Only call this once you are sure — the
    operator sees exactly this diff before it reaches anyone.

    Args:
        diff: the change, as a unified diff, ready to be read as-is.
    """
    return diff


#: What an agent that may change code is given: the patch tool, and a way to
#: refuse. Both, always — the prompt asks it to refuse when the fix is not
#: obvious, and a refusal written as prose was once proposed as the diff
#: because the code never checked for the sentinel it asked for.
FIX_TOOLS = [apply_fix, hand_over]
