"""What to do about a task, decided by ordinary branching.

Deterministic on purpose. These rules are inspectable, free to run, and identical
every time — which is what makes the first weeks of logs worth reading. A
workflow is promoted to something agentic per type, once the deterministic one
has proven itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from friday.triage import ApiIssueParams

__all__ = ["Action", "Ask", "Park", "plan_api_issue"]


@dataclass(frozen=True, slots=True)
class Ask:
    """Ask the reporter for something. The text is ready to send."""

    text: str


@dataclass(frozen=True, slots=True)
class Park:
    """Nothing can be done automatically. A human picks it up."""

    reason: str


Action = Ask | Park


def plan_api_issue(params: ApiIssueParams) -> Action:
    """A report is only actionable once it can be traced.

    A correlation id or a curl is what makes a specific request findable in the
    logs. An environment narrows the search but cannot locate anything on its
    own, so it is asked for alongside — never instead.
    """
    if params.correlation_id or params.curl:
        return Park("has enough to trace")

    wanted = ["the correlationId, or the curl you used"]
    if not params.environment:
        wanted.insert(0, "which environment you're on")
    return Ask(
        "Could you send " + " and ".join(wanted) + "? "
        "I'll trace it from there."
    )
