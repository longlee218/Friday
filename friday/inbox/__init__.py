from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from friday.config import IngestConfig
from friday.db import Database
from friday.models import InboundEvent, MentionType

__all__ = ["Inbox"]

log = logging.getLogger(__name__)

_LIVE_STREAM_ENDED = object()


class Inbox:
    """The only way into the system.

    Its entire interface is `stream()`. Everything a caller would otherwise have
    to know — which delivery path an event arrived on, whether it has been seen
    before, whether it is in scope — is handled behind it. Nothing outside this
    package should import its internals.
    """

    def __init__(self, *, provider, db: Database, config: IngestConfig) -> None:
        self._provider = provider
        self._db = db
        self._config = config

    async def stream(self) -> AsyncIterator[InboundEvent]:
        """Yield in-scope mentions, each exactly once, already persisted.

        Merges the live connection with the periodic recovery sweep. Callers
        cannot tell which path an event arrived on, and never see it twice.
        """
        queue: asyncio.Queue = asyncio.Queue()
        workers = [
            asyncio.create_task(self._pump_live(queue)),
            asyncio.create_task(self._sweep_periodically(queue)),
        ]
        try:
            while True:
                item = await queue.get()
                if item is _LIVE_STREAM_ENDED:
                    return
                yield item
        finally:
            for worker in workers:
                worker.cancel()

    async def _pump_live(self, queue: asyncio.Queue) -> None:
        async for event in self._provider.stream():
            accepted = await self._accept(event)
            if accepted is not None:
                await queue.put(accepted)
        await queue.put(_LIVE_STREAM_ENDED)

    async def _sweep_periodically(self, queue: asyncio.Queue) -> None:
        """Sweep on a timer, and immediately whenever the provider reconnects.

        A reconnect means an outage just ended, which is exactly when a gap is
        most likely — waiting out the interval would leave it open.
        """
        while True:
            try:
                await asyncio.wait_for(
                    self._provider.reconnected.wait(),
                    timeout=self._config.sweep_interval_seconds,
                )
                self._provider.reconnected.clear()
                trigger = "reconnect"
            except TimeoutError:
                trigger = "timer"
            log.debug("sweeping (%s)", trigger)
            for event in await self.sweep_once():
                await queue.put(event)

    async def sweep_once(self) -> list[InboundEvent]:
        """Recover anything the live path missed. Returns what was new.

        Runs the same acceptance path as the live connection, so a message that
        arrives on both is captured exactly once.
        """
        recovered: list[InboundEvent] = []
        for channel_id in sorted(self._config.watched_channels):
            after = await self._db.cursor_for(self._provider.name, channel_id)
            async for event in self._provider.history(channel_id, after=after):
                accepted = await self._accept(event)
                if accepted is not None:
                    recovered.append(accepted)
        if recovered:
            log.info("sweep recovered %d missed message(s)", len(recovered))
        return recovered

    async def _accept(self, event: InboundEvent) -> InboundEvent | None:
        """Both delivery paths converge here. None means it was not kept."""
        # Advance before scoping: the cursor records what we have *looked at*,
        # so the sweep does not re-fetch traffic we already dropped.
        await self._db.advance_cursor(event)
        reason = self._out_of_scope_reason(event)
        if reason:
            log.debug("dropped %s: %s", event.provider_message_id, reason)
            return None
        if not await self._db.record_event(event):
            return None  # already seen on the other delivery path
        await self._db.record_session(event)
        return event

    def _out_of_scope_reason(self, event: InboundEvent) -> str | None:
        """None means in scope. A string says why it was dropped."""
        if event.mention_type is None:
            return "does not address the account"
        if event.mention_type not in self._config.mention_types:
            return f"{event.mention_type} is not a watched mention type"
        if event.mention_type is MentionType.DM:
            # DMs are scoped by being DMs; the channel whitelist doesn't apply.
            return None
        if event.channel_id not in self._config.watched_channels:
            return f"channel {event.channel_id} is not watched"
        return None
