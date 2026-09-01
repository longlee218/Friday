from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from collections.abc import AsyncIterator, Callable

import discord_self

from friday.conversation import ConversationId
from friday.models import InboundEvent, Outbound
from friday.providers import CredentialRejected
from friday.providers.discord.normalise import normalise
from friday.verdicts import mark_for

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
        #: When the gateway went away, or None while it is up. A dead
        #: connection and a quiet channel look identical without this.
        self.down_since: datetime | None = None

        # Handlers are bound by attribute name: dispatch looks up "on_" + event.
        # `Client.event` registers by the function's own __name__, so a private
        # method name would register a handler nothing ever calls.
        self._client.on_message = self._handle_message
        self._client.on_ready = self._handle_ready
        self._client.on_resumed = self._handle_resumed
        self._client.on_disconnect = self._handle_disconnect
        self._client.on_reaction_add = self._handle_reaction_add
        self._client.on_reaction_remove = self._handle_reaction_remove

    #: Called with `(provider_message_id, mark, by)` when the operator marks a
    #: classification, and with `mark=None` when they take the mark back.
    #: Set by the composition root; a provider that stored this itself would
    #: be the only one reaching past the inbox into the database.
    on_verdict: Callable[..., object] | None = None

    async def _handle_disconnect(self) -> None:
        """The library fires this on every reconnection attempt, so the first
        one is the one that counts — taking the latest would reset the clock
        forever and the alert would never fire.
        """
        if self.down_since is None:
            self.down_since = datetime.now(timezone.utc)
            log.warning("discord gateway disconnected")

    async def _handle_ready(self) -> None:
        user = self._client.user
        log.info("connected to discord as %s (%s)", user, getattr(user, "id", "?"))
        self.down_since = None
        self.reconnected.set()

    async def _handle_resumed(self) -> None:
        log.info("discord session resumed")
        self.down_since = None
        self.reconnected.set()

    async def _handle_reaction_add(self, reaction, user) -> None:
        """The operator saying a classification was right, or wrong.

        Only the watched account's own reactions count. Anyone else in the
        channel reacting to a message is reacting for their own reasons, and
        reading their thumbs-up as a judgement on our classification would be
        putting words in their mouth.
        """
        await self._verdict(reaction, user, taking_back=False)

    async def _handle_reaction_remove(self, reaction, user) -> None:
        """They took the mark back. Absence is the state that matters."""
        await self._verdict(reaction, user, taking_back=True)

    async def _verdict(self, reaction, user, *, taking_back: bool) -> None:
        if self.on_verdict is None:
            return
        me = self._client.user
        if me is None or getattr(user, "id", None) != me.id:
            return

        mark = mark_for(str(getattr(reaction, "emoji", "")))
        if mark is None:
            return  # a reaction that means nothing to us means nothing to us

        message_id = str(reaction.message.id)
        log.info(
            "operator marked %s as %s", message_id, "unmarked" if taking_back else mark
        )
        result = self.on_verdict(
            provider_message_id=message_id,
            mark=None if taking_back else mark,
            by=str(user),
        )
        if hasattr(result, "__await__"):
            await result

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

    async def history(
        self, channel_id: str, *, after: str | None
    ) -> AsyncIterator[InboundEvent]:
        """Replay a channel from a cursor, oldest first.

        The recovery path: the live connection can miss messages, and this is
        how they are found again.
        """
        async for event in self._replay(
            channel_id,
            after=_snowflake(after),
            oldest_first=True,
        ):
            yield event

    async def recent(
        self, conversation: ConversationId, *, before: str, limit: int
    ) -> AsyncIterator[InboundEvent]:
        """Read backwards from a message, then hand them back in order.

        Used once per conversation to seed context. Discord returns newest
        first when reading backwards, and a transcript out of order reads to a
        model as a different conversation.
        """
        collected = [
            event
            async for event in self._replay(
                conversation.target_id,
                before=_snowflake(before),
                limit=limit,
                oldest_first=False,
            )
        ]
        for event in reversed(collected):
            yield event

    async def _replay(
        self, channel_id: str, **filters
    ) -> AsyncIterator[InboundEvent]:
        """Normalise a slice of a channel's history.

        `is_own` is decided the same way as on the live path — by author — so a
        message recovered by the sweep is indistinguishable from one that
        arrived over the gateway.
        """
        me = self._client.user
        if me is None:
            log.warning("asked for history before the client was ready")
            return
        channel = await self._channel(int(channel_id))
        async for message in channel.history(**filters):
            yield normalise(
                message,
                me_id=me.id,
                my_role_ids=_roles_in(message.guild),
                is_own=message.author.id == me.id,
            )

    async def _channel(self, target: int):
        return self._client.get_channel(target) or await (
            self._client.fetch_channel(target)
        )

    async def send(self, row: Outbound) -> str:
        """Post into a conversation, as the watched account.

        `reply_to` hangs the message under the one it answers, which is what
        keeps a busy channel readable. `fail_if_not_exists=False` so a deleted
        message degrades to a plain post rather than losing the reply.
        """
        channel = await self._channel(int(row.conversation.target_id))
        reference = (
            discord_self.MessageReference(
                message_id=int(row.reply_to),
                channel_id=channel.id,
                fail_if_not_exists=False,
            )
            if row.reply_to is not None
            else None
        )
        # The id comes back so the outbox can recognise this message when the
        # gateway delivers it to us as one of our own.
        return str((await channel.send(row.text, reference=reference)).id)

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


def _snowflake(message_id: str | None):
    """Discord filters on an object with an id, not on a bare number."""
    return discord_self.Object(id=int(message_id)) if message_id else None


def _roles_in(guild) -> frozenset[int]:
    """The roles the watched account holds in this guild, if any."""
    member = getattr(guild, "me", None)
    return frozenset(role.id for role in getattr(member, "roles", ()))
