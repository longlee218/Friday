"""`core.shell`: `bash`, one command with full rights, here or over SSH on a
declared host (build-the-spine ticket 24).

**The read-command allowlist is deleted**, operator's own call, 2026-09-30:
"đây là máy của tôi và tôi có thể control nên tạm thời tôi cho phép Bash
được full quyền" — the other toolsets stay reads; `bash` runs as the
operator (`docs/DESIGN.md` D6, amended). What a full shell still keeps: a
timeout with a process-group kill, output capped and spilled to the
workspace over it, redaction before the model sees it, and an audit row for
every command, not only a refusal.

**One file per concern**, once the single `shell.py` carried the allowlist,
its parser and the one tool together: `run.py` the tool itself, `process.py`
running a command and killing its process group (local or SSH, with an SSH
`ControlMaster` reused per run), `exit_codes.py` what a few exit codes mean
besides failure, `spill.py` the output cap and `save_to`, `sensitive.py` the
hook ticket 27 fills (`False` for everything, for now — no deny list, a
sensitive command will wait for the operator once 27 lands), `audit.py` the
two rows this toolset writes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from friday.kernel.toolsets.bash.run import build_bash
from friday.sdk.toolset import RunContext

__all__ = ["shell_tools"]


def shell_tools(run: RunContext, *, hosts: Sequence[str], audit: Any) -> list[Any]:
    """`bash`, bound to this run, the declared `hosts` and the kernel's
    `AuditLog`."""
    return [build_bash(run, hosts=hosts, audit=audit)]
