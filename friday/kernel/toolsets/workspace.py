"""`core.workspace`: Friday's own scratch folder, one per task.

`/tmp/friday/<task_id>/` (board `domains-plug-in` ticket 03, amendment §4).
The one place Friday writes: read, write, edit and list freely inside it.
The library (0.36.0) has no delete tool, so the amendment's "delete" waits
for one. Losing it on reboot is fine — the db, backups, skills and clones
live elsewhere.

**The confinement is the library's, not ours.** pydantic-ai-harness
`FileSystem(root_dir=…)` resolves every path (symlinks included) and refuses
one that lands outside the root. This is the only module that imports
`pydantic_ai_harness` (`tests/test_core_toolsets.py` guards it), per the
reuse-before-rewrite seam rule: one module per adopted library.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from pydantic_ai_harness import FileSystem

__all__ = ["WORKSPACE_ROOT", "save", "workspace_dir", "workspace_tools"]

#: Where every task's workspace lives. A core constant, not a config knob.
WORKSPACE_ROOT = Path("/tmp/friday")


def workspace_dir(task_id: int) -> Path:
    """This task's folder, created on first use."""
    path = WORKSPACE_ROOT / str(task_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def workspace_tools(task_id: int) -> list[Any]:
    """The file tools over this task's folder — one pydantic-ai toolset,
    which the harness hands the agent beside its plain tools."""
    return [FileSystem(root_dir=workspace_dir(task_id)).get_toolset()]


async def save(task_id: int, path: str, text: str) -> str:
    """Write `text` to `path` inside this task's workspace, through the same
    confinement the agent's own `write_file` has. Returns what happened as a
    sentence, never raises: `core.shell`'s `save_to` hands it to the model."""
    (toolset,) = workspace_tools(task_id)
    parent = str(PurePosixPath(path).parent)
    try:
        if parent != ".":
            await toolset.create_directory(parent)
        await toolset.write_file(path, text)
    # The library raises `ModelRetry` (a path outside the root, a protected
    # file) with a model-safe message; naming that class would import the
    # agent SDK here, so any failure is reported the same way.
    except Exception as refused:  # noqa: BLE001
        return f"not saved: {refused}"
    return f"saved to {path} in the workspace"
