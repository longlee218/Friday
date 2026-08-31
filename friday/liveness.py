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
        extra=None,
    ) -> None:
        self._db = db
        self._interval = interval_seconds
        #: Anything the database cannot answer — chiefly what arrived and
        #: was dropped, which is stored nowhere by design.
        self._extra = extra
        self._started = datetime.now(timezone.utc)
        self._last_seen: int | None = None

    async def run_forever(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self.beat()

    async def beat(self) -> str:
        line = await self.summary()
        log.info("%s", line)
        return line

    async def summary(self) -> str:
        messages = await self._db.messages()
        untriaged = await self._db.untriaged_mentions(limit=1000)
        tasks = await self._db.tasks()
        queued = await self._db.outbound(QUEUED)
        failed = await self._db.outbound(FAILED)

        new = "" if self._last_seen is None else f" (+{len(messages) - self._last_seen})"
        self._last_seen = len(messages)

        states = _tally(t.state for t in tasks)
        parts = [
            f"alive {_since(self._started)}",
            f"messages {len(messages)}{new}",
            f"last {_ago(max((m.created_at for m in messages), default=None))}",
            f"untriaged {len(untriaged)}",
            f"tasks {states or 'none'}",
            f"outbox {len(queued)} queued",
        ]
        if self._extra is not None:
            parts.append(self._extra())
        if failed:
            # Loud, because these are messages nobody has delivered and the
            # only way anyone finds out is by being told.
            parts.append(f"{len(failed)} FAILED TO SEND")
        return " | ".join(parts)


def _tally(values) -> str:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return ", ".join(f"{n} {state}" for state, n in sorted(counts.items()))


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
