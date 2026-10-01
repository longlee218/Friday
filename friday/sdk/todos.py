"""The agent's own investigation checklist (`RunContext.todos`, build-the-spine
ticket 24): modelled on Claude Code's `TodoWrite`, not on anything a reporter
sees.

**Not grounded evidence.** `Evidence` is what a run read and can point at;
`Todos` is the plan for what to read next, written by the model about
itself. It is never shown to a reporter and never checked by a grounding
gate — the Planner's own plan (tickets 20, 22) is unchanged by it.

Travels with a stored `Ask` the same way `Evidence` does, so a continuation
picks the checklist back up rather than losing it (`friday.kernel.toolsets
.todo.write` writes it; `friday.kernel.spine.runner`'s `_encode`/`_decode`
dump and load it beside `Evidence`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

__all__ = ["Todo", "Todos"]

Status = Literal["pending", "in_progress", "completed"]


@dataclass(frozen=True, slots=True)
class Todo:
    content: str
    status: Status


@dataclass
class Todos:
    """One run's checklist. `items` is replaced whole by every `todo_write`
    call — Claude Code's own rule, never merged — so this holds exactly what
    the model last said, in the order it gave."""

    items: tuple[Todo, ...] = ()

    def dump(self) -> dict[str, Any]:
        return {
            "items": [{"content": t.content, "status": t.status} for t in self.items]
        }

    @classmethod
    def load(cls, data: dict[str, Any]) -> Todos:
        return cls(items=tuple(Todo(**item) for item in data.get("items", ())))
