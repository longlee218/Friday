from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol, runtime_checkable

from friday.domain.conversation import ConversationId
from friday.domain.models import InboundEvent, Outbound

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

    Grows as tickets need it. Platform mechanics stay entirely inside
    implementations.

    Nothing checks this at import or at construction — it is a Protocol, so a
    missing method surfaces only when something reaches for it, at run time, in
    production. `history` and `recent` were both absent for three tickets and
    the recovery sweep never ran once; `send` was absent from here while
    `friday/outbox/__init__.py` called it on every delivery. The test that
    catches that reads *this list*, so the two cannot drift apart again — it
    used to read a hand-written copy, which is how `send` came to be in one and
    not the other.
    """

    name: str

    #: Set by the provider whenever it (re)connects, so the recovery sweep can
    #: run at once instead of waiting out the timer.
    reconnected: asyncio.Event

    def send(self, row: "Outbound") -> str | None:
        """Post one outbound row, and return the message id it became.

        The id comes back so the outbox can recognise this message when the
        gateway delivers it to us as one of our own — and `None` is allowed,
        because not every platform gives one.
        """
        ...

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
        self, channel_id: str, *, after: str | None, since: datetime | None
    ) -> AsyncIterator[InboundEvent]:
        """Yield past messages in a channel, oldest first.

        This is the recovery path: the live connection can miss messages, and
        this is how they are found.

        `after` is a cursor and wins wherever there is one: the read starts
        after that message and `since` is ignored. `after=None` means there is
        no cursor — a fresh database — and `since` is what to do about it:
        read back only that far, newest end first, and still hand them over
        oldest first. Both `None` means from the beginning of the channel
        (board `work-that-has-gone-cold`, ticket 02, D8).

        **`since` carries no default, deliberately.** A default would let an
        implementation omit the parameter and fail with `TypeError` the first
        time a cold cursor met it in production, which is precisely the
        failure this protocol's own docstring opens by warning about — and it
        would buy nothing, since the one caller always passes it by name.
        """
        ...
