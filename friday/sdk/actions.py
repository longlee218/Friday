"""The workflow actions a decision about a task comes to.

Every graph a plugin ships ends in one of these, and the pool in
`friday/kernel/pool/` is what acts on it. They are
vocabulary, not mechanism — the bottom of the stack, part of the `sdk` a plugin
codes against, so a graph node reaches them by importing `sdk` only. The triage
*outcome* types (`Decided`/`make_decided`/`NeedsHuman`) are a kernel concern and
live in `friday.kernel.domain.triage`; they are not part of a plugin's contract.

`friday.sdk.workflow` re-exports these as part of the graph vocabulary a node
returns, so `sdk` imports nothing of ours. The union is `Outcome` (was
`Action`, renamed in build-the-spine ticket 06 so it no longer shadows
`friday.sdk.action.Action`); `friday.kernel.spine.plan` re-exports all four.
The file itself moves there in ticket 16, once no DAG node imports it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["Ask", "HandOver", "Outcome", "Replan", "Reply", "Retriage"]


@dataclass(frozen=True, slots=True)
class Ask:
    """Ask the reporter for something. The text is ready to send.

    An `Ask` an agent raised through `ask_reporter` is a **continuation
    point** (board `domains-plug-in`, ticket 14 §4): `history` is the run's
    message history as plain JSON data and `evidence` what it had read, so a
    reply continues the run rather than restarting it. `todos` is its
    `core.todo` checklist (build-the-spine ticket 24), carried the same way.
    All three `None` for an `Ask` written by code or the Planner.
    """

    text: str
    history: list[Any] | None = None
    evidence: Any = None
    todos: Any = None


@dataclass(frozen=True, slots=True)
class Reply:
    """An answer. Unlike an `Ask`, this waits for approval.

    The asymmetry is the point: asking for a correlationId costs a question if
    it is wrong, and asserting a cause costs the operator's credibility with
    their own team.
    """

    text: str


@dataclass(frozen=True, slots=True)
class HandOver:
    """Nothing can be done automatically. A human picks it up.

    `reason` is quoted to the operator, never sent to a reporter under the
    operator's name — a node's own finding ("the cause mentions a
    migration"), or code's own ("no workflow for this yet"). Named for what
    it does (ticket 06): a node's agent can call `hand_over(reason)` itself
    to report this the same way it reports an answer, and a graph that
    reaches its end without deciding anything hands over by code, with no
    model asked. `Park` was the name before there was a tool by that name to
    confuse it with.

    `interruption` is set only for one specific shape of hand-over (ticket
    07): a node's agent called a tool the SDK stopped to ask about — applying
    a fix, so far — rather than one that decided nothing could be done. It is
    the SDK's own run state, serialized, so approving resumes the exact call
    that stopped rather than restarting the investigation to reach it again.
    `None` for every ordinary hand-over, which is most of them.
    """

    reason: str
    interruption: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Replan:
    """An agent found its step pointed the wrong way (the core terminal tool
    `replan`): same action, new direction. `found` is what it read that the
    next plan should know (board `domains-plug-in`, ticket 13 §1)."""

    reason: str
    found: str


@dataclass(frozen=True, slots=True)
class Retriage:
    """An agent found the task is another action's work (the core terminal
    tool `retriage`): leave the contract, triage again with `found` (board
    `domains-plug-in`, ticket 16 §1)."""

    reason: str
    found: str


Outcome = Ask | Reply | HandOver
