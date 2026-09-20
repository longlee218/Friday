"""Node 2: which window to read, and what to keep of it.

The reading itself is `friday/sources/logs.py` — two back ends and only two
(D4). This is the formula over them: the window measured back from the
reporter's message, one widening when it holds nothing loud, and the
recall-first cut.

**A source that is not configured skips**, with a reason, and the graph goes
on. That is the shape the deleted five-node graph got wrong — every node of
it skipped on every run and nothing said so out loud — so the skip is an
envelope the board renders and `Diagnose` reads as `not_checked`, not a
silent empty list.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from friday.dag.api_issue.distil import distil, frames as frames_of
from friday.dag.api_issue.resolve import path_of, resolved
from friday.dag.engine import DAGDeps, DAGState, Node, envelope

__all__ = ["dossier_of", "find_request_log_node"]

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


def _when(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
                f"the {_said(FIRST_WINDOW)} before {_when(reported_at)} "
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
