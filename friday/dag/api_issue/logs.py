"""Node 2: find the reporter's request in the log.

**Two ways to read a log, and only two** (D4). Production through the devops
MCP's Loki tools; dev through `kubectl` on the dev host, reached by `ssh dev`
— not a kubeconfig on this machine, which the spec assumed and ticket 16
measured to be wrong.

Both are reads, and the guard is declared next to the source rather than
trusted to a prompt: nothing here can write to a cluster because nothing here
has a verb that writes.

**A source that is not configured skips**, with a reason, and the graph goes
on. That is the shape the deleted five-node graph got wrong — every node of
it skipped on every run and nothing said so out loud — so the skip is an
envelope the board renders and `Diagnose` reads as `not_checked`, not a
silent empty list.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any, Protocol

from friday.dag.api_issue.distil import distil, frames as frames_of
from friday.dag.api_issue.resolve import Placement, path_of, resolved
from friday.dag.engine import DAGDeps, DAGState, Node, envelope

__all__ = [
    "LogSource",
    "SshKubectlSource",
    "LokiSource",
    "dossier_of",
    "find_request_log_node",
]

log = logging.getLogger(__name__)

#: **The window is measured back from the reporter's message, not from now**
#: (D5: "path plus identifier within a window of six hours back from the
#: reporter's message"). A clock anchored to now finds nothing for a case
#: reported this morning, and every one of ticket 00's five cases is old —
#: the slice would have been unrunnable against the thing it exists to run
#: against.
FIRST_WINDOW = timedelta(minutes=30)
WIDER_WINDOW = timedelta(hours=6)

#: How far *past* the message to read. A log line is written before the
#: person complains about it, but not always before their clock says so.
MARGIN = timedelta(minutes=5)

FIRST_LINES = 400
WIDER_LINES = 2000


class LogSource(Protocol):
    """One place lines come from. Read-only by construction."""

    name: str

    async def lines(
        self, placement: Placement, *, since: datetime, until: datetime, limit: int
    ) -> list[str]: ...


@dataclass(frozen=True, slots=True)
class SshKubectlSource:
    """Dev: `kubectl` on the dev host, over SSH.

    Measured on 2026-09-18 from the operator's Mac (ticket 16): `get pods -A`
    1.5 s, `logs --tail=200` 1.2 s, `logs --since=6h` 1.5 s, each including
    the handshake. Two round trips per read, because the pod name is a
    pattern until something looks it up.

    The host is an alias in `~/.ssh/config`, non-interactive with the agent
    loaded. Nothing here supplies a password or a key: if the alias does not
    resolve, the command fails and the node's envelope says so.
    """

    name: str = "kubectl"
    host: str = "dev"
    timeout_seconds: float = 30.0

    async def lines(
        self, placement: Placement, *, since: datetime, until: datetime, limit: int
    ) -> list[str]:
        pod = await self._run(
            f"kubectl -n {shlex.quote(placement.namespace)} get pods "
            f"-o name | grep {shlex.quote(placement.pod_pattern)} | head -1"
        )
        pod = pod.strip()
        if not pod:
            raise RuntimeError(
                f"no pod matching {placement.pod_pattern!r} in namespace "
                f"{placement.namespace!r} on {self.host}"
            )
        # `--since-time` rather than `--since`: kubectl's relative form is
        # relative to *now*, and the window this node wants is around the
        # reporter's message. There is no `--until`, so lines after the
        # window are dropped here rather than on the host.
        out = await self._run(
            f"kubectl -n {shlex.quote(placement.namespace)} logs "
            f"{shlex.quote(pod)} "
            f"--since-time={shlex.quote(_rfc3339(since))} "
            f"--tail={int(limit)}"
        )
        return out.splitlines()

    async def _run(self, remote: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "ssh",
            "-o", "BatchMode=yes",
            self.host,
            remote,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_seconds
            )
        except (TimeoutError, asyncio.TimeoutError):
            proc.kill()
            # Reaped, not merely killed: without this the child stays a
            # zombie for the life of the process, and this runs on every
            # dev task.
            await proc.wait()
            raise
        if proc.returncode != 0:
            raise RuntimeError(
                f"ssh {self.host}: exit {proc.returncode} — "
                f"{err.decode(errors='replace').strip()[:400]}"
            )
        return out.decode(errors="replace")


@dataclass(frozen=True, slots=True)
class LokiSource:
    """Production: the devops MCP's Loki tools.

    **The tool's name is configuration; its arguments are not.** The session
    that measured this server (ticket 16) read its catalogue but did not run a
    query through it, so the one thing that would make either safe — a call
    that came back — has not happened. The name is the half an operator can
    correct in `config.yaml`; wrong argument names are a release, and ticket
    02 is where they stop being a guess.

    Bounded on purpose: `loki_series` over seven days is ~780 KB, and the
    spec says never to ask it unbounded from a graph.
    """

    server: Any
    name: str = "loki"
    tool: str = "loki_query_range"
    #: LogQL. `{...}` is filled with the placement's labels.
    query: str = '{{cluster="{cluster}", namespace="{namespace}", app="{app}"}}'

    async def lines(
        self, placement: Placement, *, since: datetime, until: datetime, limit: int
    ) -> list[str]:
        selector = self.query.format(
            cluster=placement.cluster,
            namespace=placement.namespace,
            app=placement.app,
        )
        result = await self.server.call_tool(
            self.tool,
            {
                "query": selector,
                "start": _rfc3339(since),
                "end": _rfc3339(until),
                "limit": int(limit),
            },
        )
        return _text_of(result).splitlines()


def _reported_at(task: Any) -> datetime:
    """When the reporter said something — what D5's window is measured back
    from.

    The task's own creation time, which is within seconds of the message that
    opened it. `now` is the fallback for a task with no timestamp at all, and
    it is the wrong answer often enough to be worth a log line rather than a
    silent substitution.
    """
    at = getattr(task, "created_at", None)
    if isinstance(at, str):
        try:
            at = datetime.fromisoformat(at)
        except ValueError:
            at = None
    if not isinstance(at, datetime):
        log.warning(
            "task %s has no usable created_at — reading the log around now "
            "instead, which is not when this was reported",
            getattr(task, "id", "?"),
        )
        return datetime.now(timezone.utc)
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _said(window: timedelta) -> str:
    hours, seconds = divmod(int(window.total_seconds()), 3600)
    return f"{hours}h" if hours and not seconds else f"{seconds // 60}m"


def _rfc3339(at: datetime) -> str:
    """The one time format both sides of this module speak."""
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text_of(result: Any) -> str:
    """Whatever an MCP tool answered, as text.

    Duck-typed rather than imported: `friday/agent/harness.py` is the one
    module that imports the SDK, and a second one here would make the SDK's
    result shape load-bearing in a graph node.
    """
    content = getattr(result, "content", result)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part if isinstance(part, str) else str(getattr(part, "text", part))
            for part in content
        )
    return str(content)


def dossier_of(result: Any) -> tuple[str, tuple[str, ...]]:
    """Node 2's envelope read back: the dossier text, and what it did not
    check."""
    return result.get("dossier", ""), tuple(result.get("not_checked", ()))


def find_request_log_node(*, timeout_seconds: float | None = None) -> Node:
    """Build node 2.

    `deps.extra["log_sources"]` is how the sources arrive — built once by
    `register_dags` from the configuration, the same way the servers are.
    """

    async def _find(state: DAGState, deps: DAGDeps) -> Any:
        placement, _ = resolved(state["resolve"])
        params = state["prepare"]
        sources: dict[str, Any] = deps.extra.get("log_sources", {})
        wanted = "loki" if placement.env == "production" else "kubectl"
        source = sources.get(wanted)
        if source is None:
            return envelope(
                "skipped",
                f"no {wanted} source is configured, so {placement.env} logs "
                "were not read at all",
            )

        needles = tuple(
            n for n in (path_of(getattr(params, "curl", None)),) if n
        )
        correlation_id = getattr(params, "correlation_id", None)
        reported_at = _reported_at(deps.task)

        try:
            lines = await source.lines(
                placement,
                since=reported_at - FIRST_WINDOW,
                until=reported_at + MARGIN,
                limit=FIRST_LINES,
            )
        except Exception as exc:  # noqa: BLE001 — a source that is down is work
            return envelope(
                "error", f"{wanted} could not be read: {type(exc).__name__}: {exc}"
            )

        dossier = distil(
            lines, correlation_id=correlation_id, matching=needles,
            max_lines=FIRST_LINES,
        )
        widened = ()
        if dossier.worth_widening:
            # One widening, and only one (spec). A window that finds nothing
            # loud twice is a window that is not the problem.
            try:
                lines = await source.lines(
                    placement,
                    since=reported_at - WIDER_WINDOW,
                    until=reported_at + MARGIN,
                    limit=WIDER_LINES,
                )
            except Exception as exc:  # noqa: BLE001
                return envelope(
                    "error",
                    f"{wanted} could not be read on the wider window: "
                    f"{type(exc).__name__}: {exc}",
                )
            dossier = distil(
                lines, correlation_id=correlation_id, matching=needles,
                max_lines=WIDER_LINES,
            )
            widened = (
                f"the {_said(FIRST_WINDOW)} before {_rfc3339(reported_at)} "
                f"held no error and no stack, so the window was widened once "
                f"to {_said(WIDER_WINDOW)}",
            )

        not_checked = (*widened, *dossier.not_checked)
        if not dossier.lines:
            return envelope(
                "empty",
                f"{wanted} returned {dossier.total} lines and none of them "
                f"names this request",
                dossier="",
                source=wanted,
                total=dossier.total,
                not_checked=list(not_checked),
            )
        return envelope(
            "ok",
            "",
            dossier=dossier.text(),
            source=wanted,
            total=dossier.total,
            kept=dossier.kept,
            has_stack=dossier.has_stack,
            # Read here rather than by the code node, off the `Dossier` that
            # is still an object — the envelope carries text, and a second
            # pattern re-finding frames in it is a second pattern to keep in
            # step with `has_stack`.
            frames=[[file, line] for file, line in frames_of(dossier)],
            not_checked=list(not_checked),
        )

    return Node("find_request_log", _find, timeout_seconds=timeout_seconds)
