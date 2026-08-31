"""Saying so, on a timer.

A working agent on a quiet afternoon and a dead agent look identical from the
outside: no messages, no replies, no errors. That is the failure mode this
whole design is built to avoid for *mentions*, and it applies just as much to
the process itself.

The counts are read from the database rather than kept in memory, so a restart
does not reset them and the line describes what is actually stored — not what
this process believes it did.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from friday.db import Database
from friday.outbox import FAILED, QUEUED

__all__ = ["Heartbeat"]

log = logging.getLogger("friday.liveness")


class Heartbeat:
    def __init__(
        self,
        *,
        db: Database,
        interval_seconds: float = 60.0,
        keep_model_calls_days: float | None = None,
        extra=None,
    ) -> None:
        self._db = db
        self._interval = interval_seconds
        #: Anything the database cannot answer — chiefly what arrived and
        #: was dropped, which is stored nowhere by design.
        self._extra = extra
        self._keep_days = keep_model_calls_days
        self._started = datetime.now(timezone.utc)
        self._last_seen: int | None = None

    async def run_forever(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self.beat()

    async def beat(self) -> str:
        # Trimming rides the beat rather than owning a loop: it is one indexed
        # delete, and this is the only thing already running on a timer.
        if self._keep_days is not None:
            removed = await self._db.trim_model_calls(keep_days=self._keep_days)
            if removed:
                log.info("trimmed %d model call(s) older than %s days",
                         removed, self._keep_days)
        line = await self.summary()
        log.info("%s", line)
        return line

    async def summary(self) -> str:
        counts = await self._db.counts()
        total = counts["messages"]
        outbound = counts["outbound"]

        new = "" if self._last_seen is None else f" (+{total - self._last_seen})"
        self._last_seen = total

        states = ", ".join(f"{n} {s}" for s, n in sorted(counts["tasks"].items()))
        parts = [
            f"alive {_since(self._started)}",
            f"messages {total}{new}",
            f"last {_ago(counts['last_message_at'])}",
            f"untriaged {counts['untriaged']}",
            f"tasks {states or 'none'}",
            f"outbox {outbound.get(QUEUED, 0)} queued",
        ]
        failed = outbound.get(FAILED, 0)
        if self._extra is not None:
            parts.append(self._extra())
        if failed:
            # Loud, because these are messages nobody has delivered and the
            # only way anyone finds out is by being told.
            parts.append(f"{failed} FAILED TO SEND")
        return " | ".join(parts)


def _since(start: datetime) -> str:
    return _duration((datetime.now(timezone.utc) - start).total_seconds())


def _ago(when: datetime | None) -> str:
    if when is None:
        return "never"
    return f"{_duration((datetime.now(timezone.utc) - when).total_seconds())} ago"


def _duration(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{int(seconds // 60)}m"
    return f"{seconds / 3600:.1f}h"
