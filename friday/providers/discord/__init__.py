from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import discord_self

from friday.models import InboundEvent
from friday.providers import CredentialRejected
from friday.providers.discord.normalise import normalise

__all__ = ["DiscordUserProvider", "is_credential_rejected"]

log = logging.getLogger(__name__)


class DiscordUserProvider:
    """Ingestion through the watched account.

    Isolated on purpose: this is the only module depending on an unofficial
    interface, and it is expected to break when the platform changes. Keep it
    thin — classification lives in `normalise`, scoping lives in the inbox.
    """

    name = "discord"

    def __init__(
        self,
        token: str,
        *,
        client: discord_self.Client | None = None,
    ):
        self._token = token
        self._client = client or discord_self.Client()
        self._incoming: asyncio.Queue[InboundEvent] = asyncio.Queue()
        self.reconnected = asyncio.Event()

        # Handlers are bound by attribute name: dispatch looks up "on_" + event.
        # `Client.event` registers by the function's own __name__, so a private
        # method name would register a handler nothing ever calls.
        self._client.on_message = self._handle_message
        self._client.on_ready = self._handle_ready
        self._client.on_resumed = self._handle_resumed

    async def _handle_ready(self) -> None:
        user = self._client.user
        log.info("connected to discord as %s (%s)", user, getattr(user, "id", "?"))
        self.reconnected.set()

    async def _handle_resumed(self) -> None:
        log.info("discord session resumed")
        self.reconnected.set()

    async def _handle_message(self, message) -> None:
        me = self._client.user
        if me is None:
            return
        event = normalise(
            message,
            me_id=me.id,
            my_role_ids=_roles_in(message.guild),
            is_own=message.author.id == me.id,
        )
        log.debug(
            "saw %s in %s (%s) from %s: %r",
            event.mention_type or "no mention",
            event.channel_id,
            event.thread_id or "no thread",
            event.author_name,
            event.text[:120],
        )
        await self._incoming.put(event)

    async def reply(self, event: InboundEvent, text: str) -> None:
        """Post a message back into the conversation the event came from.

        Sends as the watched account. Ticket 06 puts the approval gate in front
        of this; until then nothing but the smoke test should call it.
        """
        await self.send(reply_target_id(event), text)

    async def send(self, conversation_id: str, text: str) -> None:
        """Post into a conversation, as the watched account."""
        target = int(conversation_id)
        channel = self._client.get_channel(target) or await (
            self._client.fetch_channel(target)
        )
        await channel.send(text)

    async def stream(self) -> AsyncIterator[InboundEvent]:
        """Connect, then yield every message the account can see.

        Scoping is not applied here — the inbox decides what is in scope, so
        this stays a faithful view of what arrived.
        """
        connection = asyncio.create_task(self._client.start(self._token))
        try:
            while True:
                incoming = asyncio.create_task(self._incoming.get())
                done, _ = await asyncio.wait(
                    {incoming, connection}, return_when=asyncio.FIRST_COMPLETED
                )
                if incoming in done:
                    yield incoming.result()
                    continue
                # The connection ended. Surface why rather than waiting forever
                # on a queue nothing will ever fill again.
                incoming.cancel()
                self._reraise(connection)
        finally:
            connection.cancel()
            await self._client.close()


    @staticmethod
    def _reraise(connection: asyncio.Task) -> None:
        """The connection ended. Say why, in terms an operator can act on."""
        try:
            connection.result()
        except Exception as exc:
            if is_credential_rejected(exc):
                log.error(
                    "discord rejected the account credential — not retrying. "
                    "The token is dead (a password change or 2FA toggle "
                    "invalidates it); supply a new one."
                )
                raise CredentialRejected(str(exc) or "credential rejected") from exc
            log.warning("discord connection failed: %s", exc)
            raise
        raise ConnectionError("discord connection closed without an error")


def is_credential_rejected(exc: BaseException) -> bool:
    """True when reconnecting is pointless.

    Everything else — dropped sockets, server errors — is transient and the
    library's own retry loop handles it.
    """
    if isinstance(exc, discord_self.LoginFailure):
        return True
    return (
        isinstance(exc, discord_self.ConnectionClosed) and exc.code == 4004
    )


def reply_target_id(event: InboundEvent) -> str:
    """Where a reply belongs: in the thread if there was one, else the channel.

    Replying to a thread message in the parent channel loses the context the
    person was in.
    """
    return event.thread_id or event.channel_id


def _roles_in(guild) -> frozenset[int]:
    """The roles the watched account holds in this guild, if any."""
    member = getattr(guild, "me", None)
    return frozenset(role.id for role in getattr(member, "roles", ()))
