"""The 30k-character cap on what `bash` shows directly, and what `save_to`
means: given, the whole output goes to the workspace instead of the context;
not given and the output is over the cap, it is shown cut, with a note
saying how to get the rest.
"""

from __future__ import annotations

from friday.kernel.toolsets.workspace import save

__all__ = ["MAX_CHARS", "capped", "spilled"]

#: Characters shown before the rest is cut — Claude Code's own output cap is
#: line-based; a full shell's output is not reliably line-shaped (binary,
#: single huge lines), so this counts characters instead.
MAX_CHARS = 30_000


def capped(output: str) -> tuple[str, bool]:
    """`output`, cut to `MAX_CHARS`, and whether it was."""
    if len(output) <= MAX_CHARS:
        return output, False
    return output[:MAX_CHARS], True


async def spilled(task_id: int, path: str, output: str) -> str:
    """The whole (already-scrubbed) `output` written to `path` in this task's
    workspace, instead of being shown — `save_to`'s own meaning, the same as
    `core.workspace`'s other savers."""
    return await save(task_id, path, output)
