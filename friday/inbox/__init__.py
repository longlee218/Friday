from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from friday.config import IngestConfig
from friday.db import Database
from friday.models import InboundEvent, MentionType

__all__ = ["Inbox"]

log = logging.getLogger(__name__)


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
            reason = self._out_of_scope_reason(event)
            if reason:
                log.debug("dropped %s: %s", event.provider_message_id, reason)
                continue
            if await self._db.record_event(event):
                await self._db.record_session(event)
                yield event

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
