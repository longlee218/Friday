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
from friday.conversation import ConversationId
from friday.outbox import FAILED, QUEUED, Kind

__all__ = ["Heartbeat", "Liveness"]

log = logging.getLogger("friday.liveness")


class Heartbeat:
    def __init__(
        self,
        *,
        db: Database,
        interval_seconds: float = 60.0,
        keep_model_calls_days: float | None = None,
        liveness: "Liveness | None" = None,
        promotion=None,
        context_rebuilder=None,
        extra=None,
    ) -> None:
        self._db = db
        self._interval = interval_seconds
        #: Anything the database cannot answer — chiefly what arrived and
        #: was dropped, which is stored nowhere by design.
        self._extra = extra
        self._keep_days = keep_model_calls_days
        self._liveness = liveness
        self._promotion = promotion
        #: Rebuilds channel context files, but only when promotion actually
        #: promoted something — a rebuild on every idle beat would cost a
        #: summary call for nothing new to say.
        self._context_rebuilder = context_rebuilder
        self._started = datetime.now(timezone.utc)
        self._last_seen: int | None = None

    async def run_forever(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self.beat()
            if self._liveness is not None:
                await self._liveness.check()
            await self.promote()

    async def promote(self) -> None:
        """Run one promotion pass, and rebuild channel context if it changed
        anything. A tick that promoted nothing rebuilds nothing — a summary
        call on every idle beat would cost real money for no new context."""
        if self._promotion is None:
            return
        promoted = await self._promotion.run_once()
        if promoted and self._context_rebuilder is not None:
            await self._context_rebuilder.rebuild_all()

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


#: Alerts are not about a conversation, but the outbox stores one. This names
#: the fact rather than leaving an empty string to be puzzled over.
NOWHERE = ConversationId("system", "liveness")


class Liveness:
    """Tells the operator when something is wrong, and when it stops being.

    A dead connection and a quiet afternoon are indistinguishable from outside,
    and the whole system is built to make that distinction for *mentions*. It
    applies at least as much to the process holding them.

    Messages go out through the outbox like everything else: they retry, and one
    that could not be delivered shows on the board rather than vanishing.
    """

    def __init__(
        self,
        *,
        db: Database,
        gateway,
        operator: str = "discord_bot",
        down_after_seconds: float = 300.0,
        summary_at_hour: int | None = 9,
    ) -> None:
        self._db = db
        self._gateway = gateway
        self._operator = operator
        self._down_after = down_after_seconds
        self._summary_hour = summary_at_hour
        self._told_about: datetime | None = None

    async def check(self, *, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        await self._connection(now)
        await self._summary(now)

    async def _connection(self, now: datetime) -> None:
        down_since = self._gateway.down_since
        if down_since is None:
            if self._told_about is not None:
                # Said, because otherwise the operator is left believing it is
                # still down and acting on that.
                self._told_about = None
                await self._say("Discord is back. Anything missed is swept up.")
            return
        if self._told_about is not None:
            return  # once; a repeat is an alert you learn to ignore
        # Discord drops and resumes constantly, and alerting on a blip trains
        # the operator to ignore the one that matters.
        down_for = (now - down_since).total_seconds()
        if down_for < self._down_after:
            return
        self._told_about = down_since
        await self._say(
            f"Discord has been disconnected for {_duration(down_for)}. "
            "Nothing is being captured."
        )

    async def _summary(self, now: datetime) -> None:
        """Once a day, and once a day across restarts.

        The guard used to be an attribute, which meant every start of the
        process was a fresh day: restart it four times and the operator gets
        four "Alive." messages — and with `capture_own_messages` on, four
        classifications of them. A crash loop would have sent one per attempt.

        SQLite is the only state store, and the outbox row is already the
        record of having said it. So the question is asked of the row.
        """
        if self._summary_hour is None or now.hour < self._summary_hour:
            return
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if await self._db.said_since(Kind.SUMMARY, since=midnight):
            return
        counts = await self._db.counts()
        tasks = sum(counts["tasks"].values())
        await self._say(
            f"Alive. {counts['messages']} messages held, {tasks} tasks, "
            f"{counts['untriaged']} waiting to be looked at.",
            kind=Kind.SUMMARY,
        )

    async def _say(self, text: str, *, kind: Kind = Kind.ALERT) -> None:
        await self._db.queue_outbound(
            task_id=None,
            conversation=NOWHERE,
            kind=kind,
            sender=self._operator,
            text=text,
        )
        log.info("told the operator: %s", text)
