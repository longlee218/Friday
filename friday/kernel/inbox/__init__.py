from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from collections.abc import AsyncIterator

from friday.kernel.config import IngestConfig
from friday.store.db import Database
from friday.kernel.domain.models import InboundEvent, MentionType

__all__ = ["Inbox"]

log = logging.getLogger(__name__)

#: Named because `_handle` overrules exactly this one, and a string compared
#: in two places is a string that gets edited in one of them.
_NO_MENTION = "does not address the account"
#: Not "from the watched account" — that is `is_own`, and it only knows one of
#: the two identities this process runs. This is "this process posted it",
#: whichever of them did.
_OURS = "posted by this process"
#: What counts as the watched account deliberately addressing itself. `DM` is
#: absent on purpose — see `_out_of_scope_reason`.
_TAGGED = frozenset({MentionType.DIRECT, MentionType.ROLE})

_LIVE_STREAM_ENDED = object()

#: How long somebody has to be quiet — not sending, not typing — before what
#: they said counts as finished and is read. People send one thought in three
#: messages; this is what makes it one input instead of three. Triage reads
#: the same number to decide a turn is over.
TURN_SECONDS = 12.0
#: How often the recovery sweep runs. The live connection is the fast path;
#: this only exists to close gaps it missed (it also runs on reconnect).
SWEEP_INTERVAL_SECONDS = 300.0
#: How many prior messages to pull in when a conversation first mentions us.
CONTEXT_MESSAGES = 20
#: How old a turn may be before this system stops acting on it. **One number,
#: two readers**: triage marks an older turn `outdated` and never sends it to a
#: model; the recovery sweep, meeting a channel with no cursor, uses it as how
#: far back to read (board `work-that-has-gone-cold`, ticket 02, D8). The same
#: policy seen from two sides — "work this old is not worth starting" — so it
#: is written once, here, and triage imports it.
MAX_MESSAGE_AGE_SECONDS = 24 * 3600


class Inbox:
    """The only way into the system.

    Its entire interface is `stream()`. Everything a caller would otherwise have
    to know — which delivery path an event arrived on, whether it has been seen
    before, whether it is in scope — is handled behind it. Nothing outside this
    package should import its internals.
    """

    @classmethod
    def build(cls, config, *, provider, db: Database) -> "Inbox":
        """The inbox, read from configuration here.

        The same shape `TriageRunner.build` uses, for the same reason: which
        knobs a step has is that step's business, and the composition root
        must not read them. The cold-start lookback is
        `MAX_MESSAGE_AGE_SECONDS`, the number triage shares.
        """
        return cls(
            provider=provider,
            db=db,
            config=config.ingest,
            cold_start_lookback=MAX_MESSAGE_AGE_SECONDS,
        )

    def __init__(
        self,
        *,
        provider,
        db: Database,
        config: IngestConfig,
        cold_start_lookback: float | None = None,
        turn_seconds: float = TURN_SECONDS,
        sweep_interval_seconds: float = SWEEP_INTERVAL_SECONDS,
        context_messages: int = CONTEXT_MESSAGES,
    ) -> None:
        self._provider = provider
        self._db = db
        self._config = config
        self._turn_seconds = turn_seconds
        self._sweep_interval = sweep_interval_seconds
        self._context_messages = context_messages
        #: How far back a sweep reads when a channel has no cursor, in
        #: seconds. `None` means from the beginning of the channel, which is
        #: what "no cutoff configured" already means everywhere else.
        self._cold_start_lookback = cold_start_lookback
        #: Channels already reported as cold. D11 asks for one line at boot,
        #: and a cold cursor is not a one-shot state: a watched channel whose
        #: lookback contains no messages records no cursor, so it is cold
        #: again on the next sweep and every sweep after it. Told once, it
        #: stays worth reading; told every five minutes, it is what a person
        #: learns to scroll past.
        self._reported_cold: set[str] = set()
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
                    timeout=self._sweep_interval,
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

        A channel with a cursor is asked for what comes after it. A channel
        with none is asked for the last `cold_start_lookback` instead of for
        its whole history — the cursor wins where there is one, so a channel
        quiet for longer than the lookback is not overruled by it.
        """
        recovered: list[InboundEvent] = []
        for channel_id in sorted(self._config.watched_channels):
            after = await self._db.cursor_for(self._provider.name, channel_id)
            since = self._lookback_for(channel_id) if after is None else None
            async for event in self._provider.history(
                channel_id, after=after, since=since
            ):
                accepted = await self._accept(event)
                if accepted is not None:
                    recovered.append(accepted)
        if recovered:
            log.info("sweep recovered %d missed message(s)", len(recovered))
        return recovered

    def _lookback_for(self, channel_id: str) -> datetime | None:
        """Where a cold cursor starts reading, and a line saying so.

        A cold cursor always means the same thing — this process has no record
        of ever having read this channel — and it always follows downtime,
        because the gateway was not running either while the record was being
        lost. So the gap beyond the lookback is dropped, and that is only an
        acceptable trade while somebody can see it happening. Hence the line:
        the never-drop rule is about a mention leaving no trace, and a bounded
        decision nobody is told about leaves none.
        """
        first_time = channel_id not in self._reported_cold
        self._reported_cold.add(channel_id)
        if self._cold_start_lookback is None:
            if first_time:
                log.info(
                    "%s has no cursor and no lookback is configured — "
                    "reading from the beginning of the channel",
                    channel_id,
                )
            return None
        since = datetime.now(timezone.utc) - timedelta(
            seconds=self._cold_start_lookback
        )
        if first_time:
            log.info(
                "%s has no cursor — reading back %.0fh, to %s; "
                "anything older than that is not recovered",
                channel_id,
                self._cold_start_lookback / 3600,
                since.isoformat(timespec="seconds"),
            )
        return since

    def still_typing(self, conversation: ConversationId, author_id: str) -> bool:
        """Whether this person was seen typing within the turn window.

        Handed to the triage runner as a callable so it never learns what a
        provider is. A provider without a typing signal — the fake, a platform
        that does not send one — simply never extends a turn.
        """
        seen = getattr(self._provider, "typing_at", lambda *_: None)(
            conversation.channel_id, author_id
        )
        if seen is None:
            return False
        age = (datetime.now(timezone.utc) - seen).total_seconds()
        return age < self._turn_seconds

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
        if reason is None and await self._db.we_sent(
            event.provider, event.provider_message_id, event.text
        ):
            # Last, and it overrules the reply rule above: a message this
            # process posted is never work, even when it replies to us.
            #
            # `is_own` does not cover this. It is decided on the user gateway
            # as `author.id == me.id`, and `me` is the *user* account — so
            # everything the **bot** posts reads as a stranger's. The bot DMs
            # the operator, that DM arrives back through the user gateway, and
            # a DM bypasses the channel whitelist, so it went into the triage
            # queue. The agent classified its own liveness summary and sent
            # the operator "Nothing I can do with this" quoting itself.
            #
            # `we_sent` matches on the id *or* the text, which is what closes
            # the window between the outbox posting and recording the id it
            # got back. It was written for exactly this and its only caller
            # was removed by ticket 37, leaving the guard dead.
            reason = _OURS
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
            limit=self._context_messages,
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
        if event.is_own and event.mention_type not in _TAGGED:
            # The account's own messages do not create work: the operator
            # answering somebody is what *ends* a task, and the pool reads
            # that from here. They are still kept whenever the conversation is
            # tracked, because a conversation missing one side of itself does
            # not read.
            #
            # **Unless they tagged themselves**, which nobody does by accident
            # and which is the only way to exercise the whole path — gateway,
            # mention detection, whitelist, turn, reply threading — without a
            # second Discord account. It worked until ticket 37 made this
            # unconditional; what that ticket was closing was the agent's own
            # replies coming back through the gateway and opening a task each,
            # and `we_sent` below now catches those by id or text whichever
            # identity posted them.
            #
            # `DM` is deliberately not a tag. Every message in a one-to-one DM
            # carries `MentionType.DM` whether or not anyone was named, so
            # counting it here would make every "ok" the operator types in a
            # DM open a task — the same loop, through a different door.
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
