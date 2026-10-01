"""`core.shell`'s `bash`: one command, full rights, here or over a declared
SSH host (build-the-spine ticket 24).

Declared explicitly, not from a docstring (ticket 23/26's rule):
`description=` is rendered by `_bash_description` below, each parameter's by
a `Field` beside it, and the one semantic refusal that needs no I/O — `sleep`
used to wait rather than to run something — is `args_validator=`, before this
body runs at all. An undeclared host and a sensitive command are refused in
the body: both need an audit write, which is I/O.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Annotated, Any

from pydantic import Field

from friday.kernel.harness.harness import ModelRetry, tool
from friday.kernel.toolsets.bash.audit import record_ran, record_refused
from friday.kernel.toolsets.bash.exit_codes import exit_meaning
from friday.kernel.toolsets.bash.process import LOCAL
from friday.kernel.toolsets.bash.process import run as run_process
from friday.kernel.toolsets.bash.sensitive import sensitive
from friday.kernel.toolsets.bash.spill import MAX_CHARS, capped, spilled
from friday.kernel.toolsets.workspace import workspace_dir
from friday.sdk.redact import scrub
from friday.sdk.toolset import RunContext

__all__ = ["DEFAULT_TIMEOUT_MS", "MAX_TIMEOUT_MS", "build_bash"]

log = logging.getLogger(__name__)

#: Claude Code's own numbers (`src/utils/timeouts.ts`): 2 minutes by default,
#: 10 the most any one call may ask for.
DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 600_000

#: `sleep` leading the command, the way Claude Code reads it: blocked at 2
#: seconds or more, whatever follows it. A no-I/O check, so it runs in
#: `args_validator` rather than the body.
_SLEEP_POLL = re.compile(r"^\s*sleep\s+([0-9]+(?:\.[0-9]+)?)\b")


def _bash_description() -> str:
    return (
        "Run any command, as the operator: no allowlist, full rights, on "
        "this machine or a declared host. Prefer read, grep and glob for "
        "the room's repositories rather than cat, grep, find, ls or sed "
        "here — they cite lines, and read at the running tag when you give "
        "one; `git show`/`git log` between tags is fine in bash, reading a "
        "file at the running tag is not. Give `host` to reach a declared "
        "SSH alias rather than typing `ssh` yourself — the connection is "
        "reused across calls. Output is stdout then stderr, redacted, "
        f"capped at {MAX_CHARS} characters; give `save_to` to capture "
        "everything in your workspace instead of what is shown here. Do "
        "not `sleep` to wait on something finishing — poll with a real "
        "check; `sleep N` with N ≥ 2 leading the command is refused."
    )


def _validate_bash(ctx: Any, **kwargs: Any) -> None:
    command = str(kwargs.get("command", ""))
    found = _SLEEP_POLL.match(command)
    if found and float(found.group(1)) >= 2:
        raise ModelRetry(
            "`sleep` to wait is refused — poll with a real check instead of "
            "waiting blind, or keep a delay under 2 seconds."
        )


def build_bash(run: RunContext, *, hosts: Any, audit: Any):
    """`bash`, bound to this run, the declared `hosts` and the kernel's
    `AuditLog`. `audit` writes a row for every command — refused or run."""
    declared = tuple(hosts)

    @tool(description=_bash_description(), args_validator=_validate_bash)
    async def bash(
        command: Annotated[
            str, Field(description="The command, as you would type it in a shell.")
        ],
        host: Annotated[
            str,
            Field(description='Where to run it: "local", or a declared SSH alias.'),
        ] = LOCAL,
        timeout_ms: Annotated[
            int,
            Field(
                description=f"Up to {MAX_TIMEOUT_MS}ms "
                f"({MAX_TIMEOUT_MS // 60000} minutes). Default "
                f"{DEFAULT_TIMEOUT_MS}ms ({DEFAULT_TIMEOUT_MS // 60000} minutes)."
            ),
        ] = DEFAULT_TIMEOUT_MS,
        save_to: Annotated[
            str,
            Field(
                description="A path in your workspace to write the whole "
                "output to, instead of reading it here."
            ),
        ] = "",
    ) -> str:
        """`core.shell`'s `bash` — see `_bash_description` for what the
        model is told; the docstring here is for a reader of this file,
        never the schema."""
        if host not in declared:
            reason = f"host {host!r} is not declared; declared: {list(declared)}"
            log.info("bash refused on %s: %r (%s)", host, command, reason)
            await record_refused(
                audit, task_id=run.task_id, host=host, command=command, reason=reason
            )
            return f"refused: {reason}. Nothing ran."

        if sensitive(command, host):
            reason = "this command is sensitive and needs the operator's approval"
            log.info("bash refused on %s: %r (%s)", host, command, reason)
            await record_refused(
                audit, task_id=run.task_id, host=host, command=command, reason=reason
            )
            return f"refused: {reason}. Nothing ran."

        control_path = (
            None if host == LOCAL else workspace_dir(run.task_id) / f".ssh-{host}.sock"
        )
        timeout = max(1, min(int(timeout_ms), MAX_TIMEOUT_MS)) / 1000
        started = time.monotonic()
        code, output = await run_process(
            host, command, timeout=timeout, control_path=control_path
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        log.info("bash ran on %s: %r -> exit %s", host, command, code)
        await record_ran(
            audit,
            task_id=run.task_id,
            host=host,
            command=command,
            exit_code=code,
            duration_ms=duration_ms,
        )

        output = scrub(output)
        meaning = exit_meaning(command, code) if code >= 0 else None
        header = f"exit {code}" + (f" ({meaning})" if meaning else "")

        if save_to:
            saved = await spilled(run.task_id, save_to, output)
            return f"{header}, {len(output)} chars; {saved}"

        shown, truncated = capped(output)
        lines = shown.splitlines()
        text = (
            run.evidence.show(lines) if run.evidence is not None else "\n".join(lines)
        )
        if truncated:
            text += (
                f"\n({len(output) - MAX_CHARS} more characters not shown — "
                "narrow the command or give save_to to capture all of it)"
            )
        return f"{header}\n{text}"

    return bash
