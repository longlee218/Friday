from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from friday.conversation import ConversationId
from friday.models import InboundEvent

__all__ = ["CredentialRejected", "Provider"]


class CredentialRejected(Exception):
    """The account credential is no longer valid.

    Terminal by nature: no amount of retrying fixes it, only a human supplying
    a new credential. Raised so the process can stop loudly rather than
    reconnect forever while receiving nothing.
    """


@runtime_checkable
class Provider(Protocol):
    """A chat platform, as the rest of the system sees it.

    Grows as tickets need it: reply and approval arrive with the tickets that
    use them. Platform mechanics stay entirely inside implementations.
    """

    name: str

    #: Set by the provider whenever it (re)connects, so the recovery sweep can
    #: run at once instead of waiting out the timer.
    reconnected: asyncio.Event

    def stream(self) -> AsyncIterator[InboundEvent]:
        """Yield normalised inbound messages as they arrive."""
        ...

    def recent(
        self, conversation: ConversationId, *, before: str, limit: int
    ) -> AsyncIterator[InboundEvent]:
        """Yield up to `limit` messages immediately preceding `before`.

        Used once per conversation, to seed context at the moment it first
        becomes relevant.
        """
        ...

    def history(
        self, channel_id: str, *, after: str | None
    ) -> AsyncIterator[InboundEvent]:
        """Yield past messages in a channel, oldest first, after a message id.

        `after=None` means from the beginning. This is the recovery path: the
        live connection can miss messages, and this is how they are found.
        """
        ...
