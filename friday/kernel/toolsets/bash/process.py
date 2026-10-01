"""Running one command, locally or over a declared SSH host, in its own
process group — so a timeout kills the whole pipeline, not only the `sh` or
`ssh` in front of it.

SSH reuses one connection per run through a `ControlMaster`, its socket in
the task's own workspace (`ControlPersist` 60s outlives one command, not the
run): `ssh -o BatchMode=yes -o ConnectTimeout=10 -o ControlMaster=auto -o
ControlPath=<workspace>/.ssh-<host>.sock -o ControlPersist=60s -- <host>
<command>`.
"""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

__all__ = ["LOCAL", "run"]

#: The host name that means "this machine" rather than an SSH alias.
LOCAL = "local"


def _argv(host: str, command: str, control_path: Path | None) -> list[str]:
    if host == LOCAL:
        return ["sh", "-c", command]
    assert control_path is not None
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ControlMaster=auto",
        "-o",
        f"ControlPath={control_path}",
        "-o",
        "ControlPersist=60s",
        "--",
        host,
        command,
    ]


async def run(
    host: str, command: str, *, timeout: float, control_path: Path | None
) -> tuple[int, str]:
    """`(exit code, output)` of `command` on `host`. stderr is folded into
    stdout so a failure explains itself. `-1` and a stopped-after note when
    `timeout` is exceeded — the whole process group is killed, so a
    pipeline's later stages cannot outlive its first."""
    process = await asyncio.create_subprocess_exec(
        *_argv(host, command, control_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        out, _ = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        kill_process_group(process.pid)
        await process.wait()
        return -1, f"(stopped after {timeout:.0f}s)"
    return await process.wait(), out.decode(errors="replace")


def kill_process_group(pid: int) -> None:
    """`SIGKILL` the whole group a `start_new_session=True` process leads —
    the pipeline's later stages, not only the one this process object names."""
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # already gone
