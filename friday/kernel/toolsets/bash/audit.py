"""Writing `bash`'s two audit rows: every command it actually ran, and every
one it refused — through the kernel's one `AuditLog` (`friday.kernel.audit`).
Isolated here so `run.py` reads as the command's own story, not the record of
it.
"""

from __future__ import annotations

from typing import Any

__all__ = ["record_ran", "record_refused"]

#: The toolset name `audit_log` records this under — unchanged by the move
#: off the read-command allowlist (ticket 24): still `core.shell`.
TOOLSET = "core.shell"


async def record_ran(
    audit: Any,
    *,
    task_id: int,
    host: str,
    command: str,
    exit_code: int,
    duration_ms: int,
) -> None:
    await audit.shell_ran(
        task_id=task_id,
        toolset=TOOLSET,
        host=host,
        command=command,
        exit_code=exit_code,
        duration_ms=duration_ms,
    )


async def record_refused(
    audit: Any, *, task_id: int, host: str, command: str, reason: str
) -> None:
    await audit.shell_refused(
        task_id=task_id, toolset=TOOLSET, host=host, command=command, reason=reason
    )
