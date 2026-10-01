"""`core.todo`'s `todo_write`: the agent's own investigation checklist,
replaced whole on every call.

Declared explicitly, not from a docstring: `description=` is rendered by
`_write_description` below, modelled on Claude Code's own `TodoWrite`
wording (use it for three or more steps, mark one `in_progress` before
starting it, `completed` only once it is fully done). The one rule Claude
Code leaves to the prompt is enforced here instead: at most one
`in_progress`, checked in `args_validator` since it needs no I/O.
"""

from __future__ import annotations

from typing import Annotated, Any, cast, get_args

from pydantic import Field

from friday.kernel.harness.harness import ModelRetry, tool
from friday.sdk.todos import Status, Todo, Todos

__all__ = ["build_todo_write"]

_STATUSES = get_args(Status)


def _write_description() -> str:
    return (
        "Replace your investigation checklist with this whole list, in "
        "order — not merged with what was there, it is gone. Use it for "
        "three or more distinct steps; skip it for one straightforward "
        "step. Mark a step in_progress before you start it, and completed "
        "only once it is fully done — never while something about it is "
        "still failing or unresolved. Exactly one step may be in_progress "
        "at a time. Not shown to the reporter: this is your own plan, not "
        "evidence."
    )


def _validate_write(ctx: Any, **kwargs: Any) -> None:
    todos = kwargs.get("todos") or []
    for t in todos:
        status = t.get("status") if isinstance(t, dict) else None
        if status not in _STATUSES:
            raise ModelRetry(f"status must be one of {_STATUSES}, not {status!r}.")
    in_progress = sum(1 for t in todos if t.get("status") == "in_progress")
    if in_progress > 1:
        raise ModelRetry(
            f"{in_progress} todos are in_progress; exactly one may be at a time — "
            "finish or park the others first."
        )


def build_todo_write(state: Todos):
    """`todo_write`, bound to this run's checklist (`state`, a `Todos` —
    mutated in place so the same instance a continued `Ask` carries on sees
    every call this run made)."""

    @tool(description=_write_description(), args_validator=_validate_write)
    async def todo_write(
        todos: Annotated[
            list[dict[str, str]],
            Field(
                description="The whole list, replacing what was there. Each "
                "item: {content: what to do, status: "
                "pending|in_progress|completed}."
            ),
        ],
    ) -> str:
        """`core.todo`'s `todo_write` — see `_write_description` for what
        the model is told; the docstring here is for a reader of this file,
        never the schema."""
        # `status`'s shape is already known good: `args_validator`
        # (`_validate_write`) checked every entry against `_STATUSES` before
        # this body runs at all.
        items = tuple(
            Todo(content=str(t["content"]), status=cast(Status, t["status"]))
            for t in todos
        )
        state.items = items
        in_progress = sum(1 for t in items if t.status == "in_progress")
        return f"{len(items)} todos set, {in_progress} in progress."

    return todo_write
