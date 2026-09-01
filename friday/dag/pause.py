"""A node stopping to ask, rather than guessing.

The fourth outcome. `Ask`, `Reply` and `Park` are what a *workflow* decides;
this is what a *node* does when it cannot decide at all — the fix is unsafe,
the evidence is ambiguous, the change touches something it was told not to
touch.

Without it, a node that reaches this point has two bad options: invent an
answer, or fail with a traceback nobody can act on. The exception carries
enough for the operator's direct message to be actionable on its own, without
opening a log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["PauseForHuman"]


@dataclass
class PauseForHuman(Exception):
    """Stop the DAG and ask the operator.

    `question` is what they are being asked, in their language. `options` are
    the answers the node would accept, when there is a closed set — an empty
    list means the question is open. `evidence` is what the node had in front
    of it, so the operator can judge without re-running anything.
    """

    question: str
    options: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    #: Which node stopped. Filled by the runner's caller when it parks the
    #: task, so resume knows where to pick up.
    node: str = ""

    def __post_init__(self) -> None:
        # Exception's own args, so `str(exc)` and traceback rendering behave.
        super().__init__(self.question)

    def __str__(self) -> str:
        if not self.options:
            return self.question
        return f"{self.question} ({' / '.join(self.options)})"
