"""Where an exchange is happening, and the rules that decide it.

One module owns this because the answer is not obvious and the pieces of it
used to be scattered: a property on the event, a `'' vs NULL` special case in
the schema, and a thread check inside the Discord adapter. Each was small;
together they were the definition of a conversation, written down nowhere.

A conversation id is provider-qualified. Chat platforms hand out ids from their
own namespaces, and two of them reusing a number would otherwise share a
history, a task and a context window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from friday.domain.models import InboundEvent

__all__ = ["ConversationId", "resolve"]

_PROVIDER = ":"
_THREAD = "/"


@dataclass(frozen=True, slots=True)
class ConversationId:
    """A channel, a thread, or a DM — on a named platform.

    A thread is its own conversation, not part of its parent channel: context
    seeded from the parent would pull in messages nobody in the thread was
    reading.
    """

    provider: str
    channel_id: str
    thread_id: str | None = None

    def __str__(self) -> str:
        """The stored form. It is one column, so this text *is* the identity.

        Neither separator occurs in a Discord snowflake, a Slack channel id or
        a Slack `thread_ts`. A provider whose ids contain one needs an encoding
        decision made here rather than at whichever call site notices first.
        """
        for part in (self.provider, self.channel_id, self.thread_id or ""):
            if _PROVIDER in part or _THREAD in part:
                raise ValueError(f"{part!r} cannot appear in a conversation id")
        thread = f"{_THREAD}{self.thread_id}" if self.thread_id else ""
        return f"{self.provider}{_PROVIDER}{self.channel_id}{thread}"

    @classmethod
    def parse(cls, raw: str) -> "ConversationId":
        provider, _, place = raw.partition(_PROVIDER)
        channel, _, thread = place.partition(_THREAD)
        return cls(provider, channel, thread or None)

    @property
    def target_id(self) -> str:
        """Where a message sent to this conversation actually goes.

        The thread when there is one: a reply belongs under the message it
        answers, which is what keeps a busy channel readable.
        """
        return self.thread_id or self.channel_id


def resolve(event: "InboundEvent") -> ConversationId:
    return ConversationId(event.provider, event.channel_id, event.thread_id)
