from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from friday.config import IngestConfig
from friday.store.db import Database
from friday.domain.models import InboundEvent, MentionType

__all__ = ["Inbox"]

log = logging.getLogger(__name__)

#: Named because `_handle` overrules exactly this one, and a string compared
#: in two places is a string that gets edited in one of them.
_NO_MENTION = "does not address the account"

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
        #: What has arrived and what became of it. Read by the heartbeat: a
        #: message that is dropped leaves no row anywhere, so without this the
        #: difference between "nothing arrived" and "everything was out of
        #: scope" is invisible — and they need completely different fixes.
        self.seen = 0
        self.kept = 0
        self.dropped: dict[str, int] = {}

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
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            for worker in workers:
                worker.cancel()

    async def _pump_live(self, queue: asyncio.Queue) -> None:
        await self._feed(queue, self._pump_live_inner(queue))

    async def _pump_live_inner(self, queue: asyncio.Queue) -> None:
        async for event in self._provider.stream():
            accepted = await self._accept(event)
            if accepted is not None:
                await queue.put(accepted)
        await queue.put(_LIVE_STREAM_ENDED)

    @staticmethod
    async def _feed(queue: asyncio.Queue, work) -> None:
        """Hand a worker's failure to the consumer.

        Nothing else puts to the queue, so a worker that dies quietly leaves
        `stream()` waiting on it for the life of the process — a stall that
        looks exactly like a quiet day.
        """
        try:
            await work
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised in the consumer
            await queue.put(exc)

    async def _sweep_periodically(self, queue: asyncio.Queue) -> None:
        await self._feed(queue, self._sweep_periodically_inner(queue))

    async def _sweep_periodically_inner(self, queue: asyncio.Queue) -> None:
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

    def tally(self) -> str:
        """One line: what arrived, and why most of it did not stay."""
        dropped = ", ".join(
            f"{n} {reason}" for reason, n in sorted(self.dropped.items())
        )
        return f"gateway {self.seen} seen, {self.kept} kept" + (
            f", dropped: {dropped}" if dropped else ""
        )

    async def _accept(self, event: InboundEvent) -> InboundEvent | None:
        """Both delivery paths converge here. None means it was not kept.

        The cursor moves **last**. It records "read up to here", and the sweep
        asks for what comes after — so advancing before the message is stored
        means a crash in between loses it for good, because nothing will look at
        that range again. Advancing after costs a re-read on the next sweep, and
        `record_message` deduplicates on `(provider, provider_message_id)`, so a
        re-read is free. It is the same at-least-once trade the outbox makes,
        for the same reason.
        """
        self.seen += 1
        kept = await self._handle(event)
        # Only reached if `_handle` returned. A failure leaves the cursor where
        # it was, and the sweep finds the message again.
        await self._db.advance_cursor(event)
        return kept

    async def _handle(self, event: InboundEvent) -> InboundEvent | None:
        reason = self._out_of_scope_reason(event)
        if reason == _NO_MENTION and await self._db.posted_by_us(event.reply_to):
            # They replied to something we posted. A reply names the message it
            # answers, and if that message is ours then this one is addressed
            # to us — whatever it does or does not @-mention.
            #
            # Without this the agent asks a question and cannot hear the
            # answer. People reply in a thread; they do not tag you again to
            # answer you. The task sat in `waiting_for_details` for ever, the
            # cap on how often we re-ask never fired because no follow-up ever
            # arrived, and the reporter had answered.
            log.debug(
                "%s answers a message of ours — in scope",
                event.provider_message_id,
            )
            reason = None
        if reason is None and event.is_own and await self._db.we_sent(
            event.provider, event.provider_message_id, event.text
        ):
            # Unconditional, and deliberately not part of the sync check above:
            # it needs the database. `capture_own_messages` turns off the rule
            # that our own account's messages create no work, so the operator
            # can test by mentioning themselves — and with it on, the agent's
            # own replies came back through the gateway, opened a task each,
            # and were answered. Every minute, in a real channel, under the
            # operator's name.
            #
            # The flag was never meant to buy that. A message the operator
            # typed and a message this process posted are different things, and
            # only the first is a test.
            reason = "posted by this agent"
        if reason:
            self.dropped[reason] = self.dropped.get(reason, 0) + 1
            if await self._db.conversation_is_tracked(event):
                await self._db.record_message(event, context_only=True)
            log.debug(
                "dropped %s from %s: %s — %r",
                event.provider_message_id,
                event.author_name,
                reason,
                event.text[:80],
            )
            return None
        if not await self._db.record_message(event):
            return None  # already seen on the other delivery path
        if await self._db.record_conversation(event):
            await self._seed_context(event)
        self.kept += 1
        log.info(
            "kept %s (%s) from %s in %s: %r",
            event.provider_message_id,
            event.mention_type,
            event.author_name,
            event.conversation,
            event.text[:120],
        )
        return event

    async def _seed_context(self, event: InboundEvent) -> None:
        """Pull in what was said before a conversation first involved us.

        Runs once per conversation. Without it the first mention has no history
        behind it, which is exactly when context matters most.
        """
        seeded = 0
        async for past in self._provider.recent(
            event.conversation,
            before=event.provider_message_id,
            limit=self._config.context_messages,
        ):
            await self._db.record_message(past, context_only=True)
            seeded += 1
        log.info(
            "seeded %d context message(s) for conversation %s",
            seeded,
            event.conversation,
        )

    def _out_of_scope_reason(self, event: InboundEvent) -> str | None:
        """None means in scope. A string says why it was dropped.

        `_NO_MENTION` is the one reason `_handle` may overrule: a reply to
        something we posted addresses us without naming us.
        """
        if event.is_own and not self._config.capture_own_messages:
            # Never trigger work from our own messages: the agent would answer
            # its own replies. It is still kept as context — a conversation
            # missing one side of itself reads strangely, and the responder
            # learns tone from these.
            return "written by the watched account"
        if event.mention_type is None:
            return _NO_MENTION
        if event.mention_type not in self._config.mention_types:
            return f"{event.mention_type} is not a watched mention type"
        if event.mention_type is MentionType.DM:
            # DMs are scoped by being DMs; the channel whitelist doesn't apply.
            return None
        if event.channel_id not in self._config.watched_channels:
            return f"channel {event.channel_id} is not watched"
        return None
