from __future__ import annotations

from collections.abc import AsyncIterator

from friday.config import IngestConfig
from friday.db import Database
from friday.models import InboundEvent, MentionType

__all__ = ["Inbox"]


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
        """Yield in-scope mentions, each exactly once, already persisted."""
        async for event in self._provider.stream():
            if not self._in_scope(event):
                continue
            if await self._db.record_event(event):
                await self._db.record_session(event)
                yield event

    def _in_scope(self, event: InboundEvent) -> bool:
        if event.mention_type is None:
            return False
        if event.mention_type not in self._config.mention_types:
            return False
        if event.mention_type is MentionType.DM:
            # DMs are scoped by being DMs; the channel whitelist doesn't apply.
            return True
        return event.channel_id in self._config.watched_channels
