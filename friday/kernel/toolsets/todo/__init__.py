"""`core.todo`: `todo_write`, the agent's own investigation checklist
(build-the-spine ticket 24), modelled on Claude Code's `TodoWrite`.

**Not grounded evidence, never shown to the reporter**: it is the model's
plan for what to check next, not something a reporter reads or a grounding
gate checks — the Planner's own plan (tickets 20, 22) is unchanged by it.
Lives on `RunContext.todos` (a `friday.sdk.todos.Todos`) so it travels with a
stored `Ask` the same way `Evidence` does.
"""

from __future__ import annotations

from typing import Any

from friday.kernel.toolsets.todo.write import build_todo_write
from friday.sdk.todos import Todos
from friday.sdk.toolset import RunContext, ToolsetSpec

__all__ = ["TODO", "todo_tools"]


def todo_tools(run: RunContext) -> list[Any]:
    """`todo_write`, over this run's checklist — a fresh one when the run was
    not given one (a factory called outside the spine, e.g. a test)."""
    state = run.todos if isinstance(run.todos, Todos) else Todos()
    return [build_todo_write(state)]


#: Needs nothing the process holds — `run.todos` alone — so, like `core.repos`,
#: this is built once and reused rather than closed over in `core_toolsets`.
TODO = ToolsetSpec(
    name="core.todo",
    description=(
        "Track your own investigation plan: todo_write replaces the whole "
        "checklist each call. Use it for three or more steps."
    ),
    factory=todo_tools,
)
