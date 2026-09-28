"""When the reporter's message anchors a read.

The fixed pre-fetch node this module built (`find_request_log_node`, the
window-and-widen formula over `plugins/devops/sources/logs.py`) is gone
(ticket 05): the Diagnose loop reads the log itself, through
`plugins.devops.investigate`. `_reported_at` survives because both that loop
and `plugins.devops.graph.intake` still need the one fact it answers — when
the reporter said something, which is what a window is measured back from.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

__all__ = ["_reported_at"]

log = logging.getLogger(__name__)


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
