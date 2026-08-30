from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import discord_self

from friday.models import InboundEvent
from friday.providers.discord.normalise import normalise

__all__ = ["DiscordUserProvider"]

log = logging.getLogger(__name__)


class DiscordUserProvider:
    """Ingestion through the watched account.

    Isolated on purpose: this is the only module depending on an unofficial
    interface, and it is expected to break when the platform changes. Keep it
    thin — classification lives in `normalise`, scoping lives in the inbox.
    """

    name = "discord"

    def __init__(self, token: str, *, client: discord_self.Client | None = None):
        self._token = token
        self._client = client or discord_self.Client()
        self._incoming: asyncio.Queue[InboundEvent] = asyncio.Queue()
        self._client.event(self._on_message)

    async def _on_message(self, message) -> None:
        me = self._client.user
        if me is None or message.author.id == me.id:
            return  # never react to our own messages
        await self._incoming.put(
            normalise(message, me_id=me.id, my_role_ids=_roles_in(message.guild))
        )

    async def stream(self) -> AsyncIterator[InboundEvent]:
        """Connect, then yield every message the account can see.

        Scoping is not applied here — the inbox decides what is in scope, so
        this stays a faithful view of what arrived.
        """
        connection = asyncio.create_task(self._client.start(self._token))
        try:
            while True:
                yield await self._incoming.get()
        finally:
            connection.cancel()
            await self._client.close()


def _roles_in(guild) -> frozenset[int]:
    """The roles the watched account holds in this guild, if any."""
    member = getattr(guild, "me", None)
    return frozenset(role.id for role in getattr(member, "roles", ()))
